"""Conservative AREA-04C retirement of stale managed area records."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia.objects.models import ObjectDB
from evennia.server.models import ServerConfig
from evennia.utils.search import search_tag
from systems.area_apply import _APPLY_LOCK, plan_fingerprint
from systems.area_diff import build_area_diff
from systems.area_resets import AREA_RESET_CONFIG_KEY, retire_area_controller
from systems.areas import (
    AREA_TAG_CATEGORY,
    EXIT_KEY_CATEGORY,
    ROOM_KEY_CATEGORY,
    AreaLoadPlan,
    AreaPlanError,
    compile_area_load_plan,
)


class AreaPruneError(ValueError):
    """A destructive retirement request failed a required safety gate."""


@dataclass(frozen=True)
class AreaPruneResult:
    """Safe audit summary; blocked records are deliberately retained."""

    area_key: str
    fingerprint: str
    exits_deleted: int
    rooms_deleted: int
    claims_deleted: int
    controller_retired: bool
    blocked: tuple[str, ...]


def prune_area(
    area_key: str,
    confirmed_fingerprint: str,
    *,
    compiler: Callable[[Sequence[str] | None], AreaLoadPlan] = compile_area_load_plan,
) -> AreaPruneResult:
    """Prune only freshly reviewed, empty stale records for one area.

    The exact fingerprint confirmation binds this destructive operation to the
    source closure that was reviewed with ``area/diff``.  A nonblocking shared
    apply lock makes concurrent apply/prune return a safe busy result.
    """
    if not _APPLY_LOCK.acquire(blocking=False):
        raise AreaPruneError("busy")
    try:
        try:
            plan = compiler([area_key])
            fingerprint = plan_fingerprint(plan)
            fresh = compiler([area_key])
        except AreaPlanError as err:
            raise AreaPruneError("invalid_plan") from err
        if (
            fingerprint != confirmed_fingerprint
            or plan_fingerprint(fresh) != fingerprint
        ):
            raise AreaPruneError("stale_review")
        if area_key not in plan.registry.manifests:
            raise AreaPruneError("unknown_area")
        if any(
            entry.kind == "blocked_conflict" for entry in build_area_diff(plan).entries
        ):
            raise AreaPruneError("live_conflict")
        with transaction.atomic():
            ServerConfig.objects.select_for_update().get_or_create(
                db_key=AREA_RESET_CONFIG_KEY,
                defaults={"db_value": {"version": 1, "controllers": {}}},
            )
            return _prune_locked(area_key, plan, fingerprint)
    finally:
        _APPLY_LOCK.release()


def _prune_locked(
    area_key: str, plan: AreaLoadPlan, fingerprint: str
) -> AreaPruneResult:
    """Delete independent safe exits, then safe rooms, retaining every blocker."""
    expected_rooms = {
        identity.split(":", 1)[1]
        for identity, _ in plan.rooms
        if identity.startswith(f"{area_key}:")
    }
    expected_exits = {
        key for owner, key, _ in plan.exits_and_doors if owner == area_key
    }
    rooms = {
        room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True)[0]: room
        for room in search_tag(area_key, category=AREA_TAG_CATEGORY)
        if len(room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True)) == 1
    }
    stale_rooms = {
        key: room for key, room in rooms.items() if key not in expected_rooms
    }
    stale_exits = [
        exit_obj
        for room in stale_rooms.values()
        for exit_obj in room.exits
        if exit_obj.tags.get(category=EXIT_KEY_CATEGORY, return_list=True)
        and exit_obj.tags.get(category=EXIT_KEY_CATEGORY, return_list=True)[0]
        not in expected_exits
    ]
    blocked: list[str] = []
    deletable_exits = []
    for exit_obj in stale_exits:
        if _has_queued_endpoint(exit_obj):
            blocked.append("exit:queued_action")
        else:
            deletable_exits.append(exit_obj)
    deleting_ids = {exit_obj.id for exit_obj in deletable_exits}
    deletable_rooms = []
    for key, room in stale_rooms.items():
        reason = _room_blocker(room, deleting_ids)
        if reason:
            blocked.append(f"room:{key}:{reason}")
        else:
            deletable_rooms.append(room)
    for exit_obj in deletable_exits:
        exit_obj.delete()
    for room in deletable_rooms:
        room.delete()
    retired = (
        not expected_rooms
        and not blocked
        and len(deletable_rooms) == len(stale_rooms)
        and len(deletable_exits) == len(stale_exits)
    )
    claims = _retire_claims(area_key) if retired else 0
    if retired:
        retire_area_controller(area_key)
    return AreaPruneResult(
        area_key,
        fingerprint,
        len(deletable_exits),
        len(deletable_rooms),
        claims,
        retired,
        tuple(sorted(set(blocked))),
    )


def _room_blocker(room: Any, deleting_exit_ids: set[int]) -> str:
    """Return one conservative reason a room cannot be retired."""
    if _configured_role(room):
        return "configured_role"
    if _has_queued_endpoint(room):
        return "queued_action"
    incoming = ObjectDB.objects.filter(db_destination=room).exclude(
        id__in=deleting_exit_ids
    )
    if incoming.exists():
        return "incoming_exit"
    outgoing_ids = {exit_obj.id for exit_obj in room.exits}
    if any(item.id not in outgoing_ids for item in room.contents):
        return "contents_or_occupant"
    return ""


def _configured_role(room: Any) -> bool:
    """Refuse a room used by any configured start, sanctuary, or recall role."""
    room_keys = room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True)
    areas = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    if len(room_keys) != 1 or len(areas) != 1:
        return True
    reference = f"{areas[0]}:{room_keys[0]}"
    return any(
        getattr(settings, name, None) == reference
        for name in (
            "CHARACTER_START_ROOM",
            "COMBAT_RESPAWN_SANCTUARY",
            "RECALL_DESTINATION",
        )
    )


def _has_queued_endpoint(target: Any) -> bool:
    """Refuse any record named by an active action's primitive arguments."""
    from systems.action_queue import inspect_action
    from typeclasses.characters import Character

    for character in Character.objects.filter_family().iterator():
        action = inspect_action(character)
        if action is not None and _contains_identifier(action, target.id):
            return True
    return False


def _contains_identifier(value: Any, identifier: int) -> bool:
    """Search primitive queued-action data without interpreting its semantics."""
    if isinstance(value, dict):
        return any(_contains_identifier(item, identifier) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_identifier(item, identifier) for item in value)
    return value == identifier


def _retire_claims(area_key: str) -> int:
    """Remove only completed retired-area reset claims under their locked records."""
    from systems.mob_spawning import MOBILE_SPAWN_CLAIMS_CONFIG_KEY
    from systems.object_spawning import OBJECT_SPAWN_CLAIMS_KEY

    removed = 0
    for config_key in (MOBILE_SPAWN_CLAIMS_CONFIG_KEY, OBJECT_SPAWN_CLAIMS_KEY):
        config = (
            ServerConfig.objects.select_for_update().filter(db_key=config_key).first()
        )
        if config is None or not isinstance(config.db_value, dict):
            continue
        state = dict(config.db_value)
        claims = dict(state.get("claims", {}))
        stale = [
            key
            for key in claims
            if isinstance(key, str) and key.startswith(f"{area_key}:")
        ]
        for key in stale:
            del claims[key]
        if stale:
            state["claims"] = claims
            config.db_value = state
            config.save(update_fields=["db_value"])
            removed += len(stale)
    return removed

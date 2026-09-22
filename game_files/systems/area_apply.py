"""Transactional AREA-04B application of a compiled source plan.

Apply owns source-authored rooms, exits, and prototype cache records.  It
intentionally does not run reset directives or touch occupants, ordinary
contents, managed survivors, current door state, homes, or reservations.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia import create_object
from evennia.prototypes.prototypes import save_prototype
from evennia.server.models import ServerConfig
from evennia.utils.search import search_tag
from systems.area_resets import AREA_RESET_CONFIG_KEY, handoff_area_controllers
from systems.areas import (
    AREA_TAG_CATEGORY,
    EXIT_KEY_CATEGORY,
    ROOM_KEY_CATEGORY,
    AreaLoadPlan,
    AreaPlanError,
    compile_area_load_plan,
    set_external_destination,
)
from systems.doors import apply_door_area_data
from systems.room_environment import ROOM_ENVIRONMENT_ATTRIBUTE
from systems.room_policy import ROOM_POLICY_ATTRIBUTE
from systems.travel import SECTOR_ATTRIBUTE


class AreaApplyError(ValueError):
    """An apply request cannot safely change the managed graph."""


@dataclass(frozen=True)
class AreaApplyResult:
    """Safe, primitive audit data from one successful apply."""

    areas: tuple[str, ...]
    fingerprint: str
    rooms_created: int
    rooms_updated: int
    exits_created: int
    exits_updated: int
    prototypes_saved: int


_APPLY_LOCK = threading.Lock()


def _primitive(value: Any) -> Any:
    """Copy frozen plan records into canonical JSON-compatible data."""
    if isinstance(value, Mapping):
        return {key: _primitive(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_primitive(item) for item in value]
    return value


def plan_fingerprint(plan: AreaLoadPlan) -> str:
    """Fingerprint exactly the immutable manifest closure being applied."""
    encoded = json.dumps(
        _primitive(plan.registry.manifests), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_areas(
    selected: Sequence[str] | None = None,
    *,
    compiler: Callable[[Sequence[str] | None], AreaLoadPlan] = compile_area_load_plan,
    stage_hook: Callable[[str], None] | None = None,
) -> AreaApplyResult:
    """Recompile immediately before applying a source closure atomically.

    A process-local nonblocking lock returns ``busy`` rather than waiting behind
    another builder operation.  The reset registry row is also locked for the
    transaction, serializing applies with reset controller handoffs.
    """
    if not _APPLY_LOCK.acquire(blocking=False):
        raise AreaApplyError("busy")
    try:
        try:
            plan = compiler(selected)
            fingerprint = plan_fingerprint(plan)
            # This second compilation is intentionally adjacent to mutation.
            # A changed source closure aborts before any database write.
            if plan_fingerprint(compiler(selected)) != fingerprint:
                raise AreaApplyError("source_changed")
        except AreaPlanError as err:
            raise AreaApplyError("invalid_plan") from err
        _validate_configured_roles(plan)
        with transaction.atomic():
            ServerConfig.objects.select_for_update().get_or_create(
                db_key=AREA_RESET_CONFIG_KEY,
                defaults={"db_value": {"version": 1, "controllers": {}}},
            )
            counts = _apply_plan(plan, stage_hook)
            handoff_area_controllers(plan, tuple(plan.registry.manifests))
        return AreaApplyResult(tuple(plan.registry.manifests), fingerprint, *counts)
    finally:
        _APPLY_LOCK.release()


def _validate_configured_roles(plan: AreaLoadPlan) -> None:
    """Reject only configured proposed roles that would lose their source room."""
    from systems.room_roles import ROOM_ROLE_SETTINGS

    expected = set(plan.registry.rooms)
    for setting in ROOM_ROLE_SETTINGS:
        raw = getattr(settings, setting, None)
        if raw is None:
            continue
        if not isinstance(raw, str) or raw.count(":") != 1:
            raise AreaApplyError("invalid_configured_role")
        area, room = (part.strip().lower() for part in raw.split(":"))
        if area in plan.registry.manifests and f"{area}:{room}" not in expected:
            raise AreaApplyError("configured_role_missing")


def _one_room(area: str, key: str) -> Any | None:
    """Find one exact live room or reject duplicate managed identity."""
    matches = [
        room
        for room in search_tag(key, category=ROOM_KEY_CATEGORY)
        if room.tags.has(area, category=AREA_TAG_CATEGORY)
    ]
    if len(matches) > 1:
        raise AreaApplyError("room_conflict")
    return matches[0] if matches else None


def _apply_plan(
    plan: AreaLoadPlan, stage_hook: Callable[[str], None] | None
) -> tuple[int, int, int, int, int]:
    """Apply ordered source records, leaving stale records intact for AREA-04C."""
    hook = stage_hook or (lambda _stage: None)
    rooms: dict[str, Any] = {}
    created_rooms = updated_rooms = created_exits = updated_exits = prototypes = 0
    hook("source_prototypes")
    for _key, prototype in plan.source_prototypes:
        save_prototype(_primitive(prototype))
        prototypes += 1
    hook("rooms")
    _apply_room_renames(plan)
    for identity, record in plan.rooms:
        area, key = identity.split(":", 1)
        room = _one_room(area, key)
        if room is None:
            room = create_object(settings.BASE_ROOM_TYPECLASS, key=record["name"])
            room.tags.add(area, category=AREA_TAG_CATEGORY)
            room.tags.add(key, category=ROOM_KEY_CATEGORY)
            created_rooms += 1
        else:
            updated_rooms += 1
        room.key = record["name"]
        room.attributes.add("desc", record["description"])
        room.attributes.add("extra_descs", _primitive(record["extra_descriptions"]))
        room.attributes.add(ROOM_POLICY_ATTRIBUTE, _primitive(record["policy"]))
        room.attributes.add(
            ROOM_ENVIRONMENT_ATTRIBUTE, _primitive(record["environment"])
        )
        room.attributes.add(SECTOR_ATTRIBUTE, record["sector"])
        profiles = room.tags.get(category="weather_profile", return_list=True)
        for profile in profiles:
            room.tags.remove(profile, category="weather_profile")
        if record["weather_profile"]:
            room.tags.add(record["weather_profile"], category="weather_profile")
        rooms[identity] = room
    hook("exits_and_doors")
    for area, exit_key, record in plan.exits_and_doors:
        source = rooms[f"{area}:{record['source_room']}"]
        destination_data = record["destination"]
        destination_area = (
            area
            if destination_data["kind"] == "local"
            else destination_data["area_key"]
        )
        destination = rooms.get(
            f"{destination_area}:{destination_data['room_key']}"
        ) or _one_room(destination_area, destination_data["room_key"])
        if destination is None:
            raise AreaApplyError("destination_missing")
        _apply_exit_renames(plan, area, source)
        matches = [
            item
            for item in source.exits
            if item.tags.has(exit_key, category=EXIT_KEY_CATEGORY)
        ]
        if len(matches) > 1:
            raise AreaApplyError("exit_conflict")
        created = not matches
        exit_obj = (
            matches[0]
            if matches
            else create_object(
                settings.BASE_EXIT_TYPECLASS,
                key=record["name"],
                location=source,
                destination=destination,
            )
        )
        if created:
            exit_obj.tags.add(exit_key, category=EXIT_KEY_CATEGORY)
            created_exits += 1
        else:
            updated_exits += 1
        exit_obj.key = record["name"]
        exit_obj.destination = destination
        exit_obj.aliases.clear()
        if record["aliases"]:
            exit_obj.aliases.add(record["aliases"])
        exit_obj.attributes.add("desc", record["description"])
        if destination_data["kind"] == "external":
            set_external_destination(
                exit_obj, destination_area, destination_data["room_key"]
            )
        # Existing door state belongs exclusively to AREA-03. New exits need an
        # authored initial configuration to become usable at all.
        if created:
            apply_door_area_data(exit_obj, _primitive(record["door"]))
    hook("service_assignments")
    hook("controller_handoff")
    return created_rooms, updated_rooms, created_exits, updated_exits, prototypes


def _apply_room_renames(plan: AreaLoadPlan) -> None:
    """Move only explicitly mapped live room identity tags in place."""
    for area, manifest in plan.registry.manifests.items():
        for old, new in manifest["renames"]["rooms"].items():
            room = _one_room(area, old)
            if room is None or _one_room(area, new) is not None:
                continue
            room.tags.remove(old, category=ROOM_KEY_CATEGORY)
            room.tags.add(new, category=ROOM_KEY_CATEGORY)


def _apply_exit_renames(plan: AreaLoadPlan, area: str, source: Any) -> None:
    """Retag an exit only when its manifest explicitly consumes its old key."""
    mapping = plan.registry.manifests[area]["renames"]["exits"]
    for old, new in mapping.items():
        if any(item.tags.has(new, category=EXIT_KEY_CATEGORY) for item in source.exits):
            continue
        matches = [
            item
            for item in source.exits
            if item.tags.has(old, category=EXIT_KEY_CATEGORY)
        ]
        if len(matches) == 1:
            matches[0].tags.remove(old, category=EXIT_KEY_CATEGORY)
            matches[0].tags.add(new, category=EXIT_KEY_CATEGORY)

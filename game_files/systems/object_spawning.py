"""AREA-03D reset-owned object roots and recoverable placement reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from evennia.prototypes.prototypes import PROTOTYPE_TAG_CATEGORY
from evennia.server.models import ServerConfig
from systems.areas import area_of, room_key_of
from systems.currency import create_pile
from systems.encumbrance import spawn_with_capacity

OBJECT_SPAWN_IDENTITY_ATTRIBUTE = "object_spawn_identity"
OBJECT_SPAWN_IDENTITY_VERSION = 1
OBJECT_SPAWN_CLAIMS_KEY = "object_spawn_claims"
OBJECT_SPAWN_CLAIMS_VERSION = 1


class ObjectSpawnError(ValueError):
    """A reset-owned object placement cannot be safely reconciled."""


@dataclass(frozen=True)
class ObjectSpawnIdentity:
    """Immutable source identity for one reset-created root only."""

    prototype_key: str
    area_key: str
    placement_key: str
    reset_room_key: str


@dataclass(frozen=True)
class ObjectSpawnResult:
    """One placement result that never exposes a live object to builders."""

    status: str
    reason: str = ""
    created: int = 0


def object_spawn_identity(item: Any) -> ObjectSpawnIdentity | None:
    """Read one strict root identity; ordinary contents never have one."""
    raw = item.attributes.get(OBJECT_SPAWN_IDENTITY_ATTRIBUTE)
    if raw is None:
        return None
    expected = {
        "version",
        "prototype_key",
        "area_key",
        "placement_key",
        "reset_room_key",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != expected
        or raw["version"] != OBJECT_SPAWN_IDENTITY_VERSION
    ):
        raise ObjectSpawnError("Object spawn identity is malformed.")
    identity = ObjectSpawnIdentity(
        raw["prototype_key"],
        raw["area_key"],
        raw["placement_key"],
        raw["reset_room_key"],
    )
    for value in (
        identity.prototype_key,
        identity.area_key,
        identity.placement_key,
        identity.reset_room_key,
    ):
        _key(value)
    return identity


def reconcile_object_placement(
    reset_token: str,
    area_key: str,
    placement_key: str,
    placement: Mapping[str, Any],
    rooms: Mapping[str, Any],
) -> ObjectSpawnResult:
    """Fill an authored root deficit without recalling or changing survivors."""
    _key(reset_token)
    _key(area_key)
    _key(placement_key)
    _validate_placement(area_key, placement_key, placement, rooms)
    claim = f"{area_key}:{reset_token}:{placement_key}"
    with transaction.atomic():
        state = _locked_claims()
        prior = state["claims"].get(claim)
        if prior and prior["status"] == "completed":
            return ObjectSpawnResult("duplicate")
        state["claims"][claim] = {"status": "claimed", "created_ids": [], "reason": ""}
        _write_claims(state)

    created = 0
    reason = ""
    while True:
        counts = object_population_snapshot(area_key)
        managed = counts.placement_counts.get(placement_key, 0)
        room_count = counts.room_prototype_counts.get(placement["room_key"], {}).get(
            placement["prototype_key"], 0
        )
        area_count = counts.area_prototype_counts.get(placement["prototype_key"], 0)
        if managed >= placement["desired"]:
            break
        if room_count >= placement["room_max"]:
            reason = "room_maximum"
            break
        if area_count >= placement["area_max"]:
            reason = "area_maximum"
            break
        root = _create_root(
            placement,
            rooms[placement["room_key"]],
            reset_token,
            area_key,
            placement_key,
        )
        if root is None:
            reason = "spawn_failed"
            break
        created += 1
        _append_claim_id(claim, root.id)
    _finish_claim(claim, created, reason)
    return ObjectSpawnResult("created" if created else "unchanged", reason, created)


@dataclass(frozen=True)
class ObjectPopulationSnapshot:
    """Fresh root-only counts, independent of names and cached membership."""

    placement_counts: dict[str, int]
    room_prototype_counts: dict[str, dict[str, int]]
    area_prototype_counts: dict[str, int]


def object_population_snapshot(area_key: str) -> ObjectPopulationSnapshot:
    """Count managed roots globally and unmanaged roots only where physical."""
    _key(area_key)
    from typeclasses.objects import Item

    placements: dict[str, int] = {}
    rooms: dict[str, dict[str, int]] = {}
    areas: dict[str, int] = {}
    for item in Item.objects.filter_family().iterator():
        try:
            identity = object_spawn_identity(item)
        except ObjectSpawnError:
            continue
        if identity is not None:
            if identity.area_key == area_key:
                placements[identity.placement_key] = (
                    placements.get(identity.placement_key, 0) + 1
                )
                areas[identity.prototype_key] = areas.get(identity.prototype_key, 0) + 1
                location = getattr(item, "location", None)
                if (
                    location is not None
                    and area_of(location) == area_key
                    and room_key_of(location)
                ):
                    room_key = room_key_of(location)
                    rooms.setdefault(room_key, {})[identity.prototype_key] = (
                        rooms.setdefault(room_key, {}).get(identity.prototype_key, 0)
                        + 1
                    )
            continue
        location = getattr(item, "location", None)
        if location is None or area_of(location) != area_key:
            continue
        prototype = _prototype_tag(item)
        room_key = room_key_of(location)
        if prototype is None or room_key is None:
            continue
        areas[prototype] = areas.get(prototype, 0) + 1
        rooms.setdefault(room_key, {})[prototype] = (
            rooms.setdefault(room_key, {}).get(prototype, 0) + 1
        )
    return ObjectPopulationSnapshot(placements, rooms, areas)


def _create_root(
    placement: Mapping[str, Any],
    room: Any,
    reset_token: str,
    area_key: str,
    placement_key: str,
) -> Any | None:
    """Build a complete root tree, deleting every new asset if any step fails."""
    root = None
    try:
        root = _spawn_node(
            placement["prototype_key"], room, f"{reset_token}:{placement_key}:root"
        )
        _write_identity(
            root,
            ObjectSpawnIdentity(
                placement["prototype_key"],
                area_key,
                placement_key,
                placement["room_key"],
            ),
        )
        for index, child in enumerate(placement["contents"]):
            _spawn_children(root, child, f"{reset_token}:{placement_key}:{index}")
        return root
    except Exception:
        if root is not None:
            root.delete()
        return None


def _spawn_children(parent: Any, node: Mapping[str, Any], token: str) -> None:
    for copy in range(node["quantity"]):
        child = _spawn_node(node["prototype_key"], parent, f"{token}:{copy}")
        for index, descendant in enumerate(node["contents"]):
            _spawn_children(child, descendant, f"{token}:{copy}:{index}")


def _spawn_node(prototype_key: str, destination: Any, token: str) -> Any:
    prototype = _prototype(prototype_key)
    if str(prototype.get("type") or "").casefold() == "money":
        amount = prototype.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ObjectSpawnError("Money prototype needs a positive amount.")
        return create_pile(
            destination, amount, token, weight=float(prototype.get("weight", 0))
        )
    spawned = spawn_with_capacity(prototype, destination, bypass_reason="area_reset")
    if len(spawned) != 1:
        raise ObjectSpawnError(
            "Object placement was denied by containment or capacity."
        )
    item = spawned[0]
    profile = item.attributes.get("item_resource")
    if profile is not None:
        from systems.item_resources import set_resource_profile

        set_resource_profile(item, profile)
    return item


def _validate_placement(
    area_key: str,
    placement_key: str,
    placement: Mapping[str, Any],
    rooms: Mapping[str, Any],
) -> None:
    expected = {
        "room_key",
        "prototype_key",
        "desired",
        "room_max",
        "area_max",
        "contents",
    }
    if set(placement) != expected or placement["room_key"] not in rooms:
        raise ObjectSpawnError("Object placement has an invalid live room.")
    for field in ("room_key", "prototype_key"):
        _key(placement[field])
    for field in ("desired", "room_max", "area_max"):
        if (
            isinstance(placement[field], bool)
            or not isinstance(placement[field], int)
            or placement[field] < 0
        ):
            raise ObjectSpawnError("Object placement has invalid limits.")
    if (
        placement["desired"] > placement["room_max"]
        or placement["desired"] > placement["area_max"]
    ):
        raise ObjectSpawnError("Object placement exceeds a ceiling.")
    if (
        area_of(rooms[placement["room_key"]]) != area_key
        or room_key_of(rooms[placement["room_key"]]) != placement["room_key"]
    ):
        raise ObjectSpawnError("Object placement live room is ambiguous.")
    _prototype(placement["prototype_key"])


def _prototype(key: str) -> dict[str, Any]:
    from systems.prototype_catalogs import (
        PrototypeCatalogError,
        resolve_runtime_prototype,
    )

    try:
        return resolve_runtime_prototype(key, kind="item")
    except PrototypeCatalogError:
        raise ObjectSpawnError("Item prototype is missing or ambiguous.")


def _prototype_tag(item: Any) -> str | None:
    keys = item.tags.get(category=PROTOTYPE_TAG_CATEGORY, return_list=True)
    return keys[0] if len(keys) == 1 else None


def _write_identity(item: Any, identity: ObjectSpawnIdentity) -> None:
    item.attributes.add(
        OBJECT_SPAWN_IDENTITY_ATTRIBUTE,
        {
            "version": OBJECT_SPAWN_IDENTITY_VERSION,
            "prototype_key": identity.prototype_key,
            "area_key": identity.area_key,
            "placement_key": identity.placement_key,
            "reset_room_key": identity.reset_room_key,
        },
    )


def _key(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in value)
    ):
        raise ObjectSpawnError("Object reset identity must be a stable key.")


def _initial_claims() -> dict[str, Any]:
    return {"version": OBJECT_SPAWN_CLAIMS_VERSION, "claims": {}}


def _locked_claims() -> dict[str, Any]:
    config, _ = ServerConfig.objects.select_for_update().get_or_create(
        db_key=OBJECT_SPAWN_CLAIMS_KEY, defaults={"db_value": _initial_claims()}
    )
    raw = config.value
    if (
        not isinstance(raw, Mapping)
        or raw.get("version") != OBJECT_SPAWN_CLAIMS_VERSION
        or not isinstance(raw.get("claims"), Mapping)
    ):
        raise ObjectSpawnError("Object reset claims are malformed.")
    return {
        "version": OBJECT_SPAWN_CLAIMS_VERSION,
        "claims": {key: dict(value) for key, value in raw["claims"].items()},
    }


def _write_claims(state: Mapping[str, Any]) -> None:
    ServerConfig.objects.conf(OBJECT_SPAWN_CLAIMS_KEY, value=dict(state))


def _append_claim_id(claim: str, identifier: int) -> None:
    with transaction.atomic():
        state = _locked_claims()
        state["claims"][claim]["created_ids"].append(identifier)
        _write_claims(state)


def _finish_claim(claim: str, created: int, reason: str) -> None:
    with transaction.atomic():
        state = _locked_claims()
        state["claims"][claim].update(
            {"status": "completed", "created": created, "reason": reason}
        )
        _write_claims(state)

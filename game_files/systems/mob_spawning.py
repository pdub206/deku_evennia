"""MOB-05's validated NPC identity, counting, and placement spawn service.

The service deliberately does not schedule resets. AREA-03 can call
``reconcile_mobile_placement`` with its durable reset token; builders and area
loading use the same fresh-copy path without accidentally creating a reset
directive. Every record persisted here is a small tree of primitive values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from evennia.prototypes.prototypes import search_prototype
from evennia.prototypes.spawner import spawn
from evennia.server.models import ServerConfig
from evennia.utils import logger
from evennia.utils.utils import inherits_from
from systems.areas import area_of, room_key_of
from systems.magic_release_manifest import is_level_published
from systems.progression import CLASS_PROGRESSION, RegistryValidationError

MOBILE_SPAWN_IDENTITY_ATTRIBUTE = "mobile_spawn_identity"
MOBILE_SPAWN_IDENTITY_VERSION = 1
MOBILE_SPAWN_CLAIMS_CONFIG_KEY = "mobile_spawn_claims"
MOBILE_SPAWN_CLAIMS_VERSION = 1
NPC_TYPECLASS = "typeclasses.characters.Character"


class MobileSpawnError(ValueError):
    """Raised when a mobile cannot safely be identified or created."""


@dataclass(frozen=True)
class MobileSpawnIdentity:
    """Immutable, source-only identity for one NPC copy."""

    prototype_key: str
    reset_owner: str | None = None
    area_key: str | None = None
    placement_key: str | None = None
    reset_room_key: str | None = None

    @property
    def managed(self) -> bool:
        """Whether this copy belongs to a specific reset placement."""
        return self.placement_key is not None


@dataclass(frozen=True)
class MobilePlacement:
    """One portable reset placement expressed only through stable keys."""

    area_key: str
    room_key: str
    placement_key: str
    prototype_key: str
    desired: int
    room_max: int
    area_max: int


@dataclass(frozen=True)
class MobilePopulationSnapshot:
    """A fresh, side-effect-free count used by reset and diagnostics callers."""

    area_key: str
    placement_counts: dict[str, int]
    room_prototype_counts: dict[str, dict[str, int]]
    area_prototype_counts: dict[str, int]


@dataclass(frozen=True)
class MobileSpawnResult:
    """One structured spawn/reconciliation outcome safe for builder feedback."""

    status: str
    reason: str = ""
    npc: Any | None = None
    created: int = 0


def mobile_spawn_identity(npc: Any) -> MobileSpawnIdentity | None:
    """Return an NPC's exact immutable identity, or ``None`` for legacy copies."""
    raw = npc.attributes.get(MOBILE_SPAWN_IDENTITY_ATTRIBUTE)
    if raw is None:
        return None
    return _identity_from_raw(raw)


def validate_mobile_placement(
    raw: Any, *, area_key: str | None = None
) -> MobilePlacement:
    """Validate one portable placement before it can affect population."""
    if isinstance(raw, MobilePlacement):
        placement = raw
    elif isinstance(raw, Mapping):
        expected = {
            "area_key",
            "room_key",
            "placement_key",
            "prototype_key",
            "desired",
            "room_max",
            "area_max",
        }
        if set(raw) != expected:
            raise MobileSpawnError("Mobile placement has an invalid shape.")
        placement = MobilePlacement(**dict(raw))
    else:
        raise MobileSpawnError("Mobile placement must be a mapping.")
    if area_key is not None and placement.area_key != area_key:
        raise MobileSpawnError("Mobile placement belongs to a different area.")
    for name in ("area_key", "room_key", "placement_key", "prototype_key"):
        _stable_key(getattr(placement, name), name)
    for name in ("desired", "room_max", "area_max"):
        value = getattr(placement, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MobileSpawnError(f"Mobile placement {name} must be non-negative.")
    if placement.desired > placement.room_max or placement.desired > placement.area_max:
        raise MobileSpawnError("Mobile placement desired population exceeds a maximum.")
    _prototype_for_key(placement.prototype_key)
    return placement


def validate_mobile_placements(
    area_key: str, placements: Sequence[Any], rooms: Mapping[str, Any]
) -> tuple[MobilePlacement, ...]:
    """Validate an area's complete placement list and stable room references."""
    _stable_key(area_key, "area_key")
    if isinstance(placements, (str, bytes)) or not isinstance(placements, Sequence):
        raise MobileSpawnError("Mobile placements must be a list.")
    validated: list[MobilePlacement] = []
    seen: set[str] = set()
    for raw in placements:
        placement = validate_mobile_placement(raw, area_key=area_key)
        if placement.placement_key in seen:
            raise MobileSpawnError(
                "Mobile placement keys must be unique within an area."
            )
        room = rooms.get(placement.room_key)
        if room is None:
            raise MobileSpawnError("Mobile placement references a missing reset room.")
        if area_of(room) != area_key or room_key_of(room) != placement.room_key:
            raise MobileSpawnError("Mobile placement reset room is ambiguous.")
        seen.add(placement.placement_key)
        validated.append(placement)
    return tuple(validated)


def spawn_mobile(
    prototype_key: str,
    room: Any,
    *,
    caller: Any | None = None,
    identity: MobileSpawnIdentity | None = None,
) -> MobileSpawnResult:
    """Create one fresh NPC through room admission and write identity at birth."""
    prototype = _prototype_for_key(prototype_key)
    if not _is_room(room):
        return MobileSpawnResult("failed", "invalid_room")
    identity = identity or MobileSpawnIdentity(prototype_key)
    if identity.prototype_key != prototype_key:
        raise MobileSpawnError("Mobile identity and prototype key disagree.")
    materialized = _flatten_prototype(prototype)
    try:
        _validate_classed_npc(materialized)
    except MobileSpawnError:
        return MobileSpawnResult("failed", "unavailable_class")
    materialized[MOBILE_SPAWN_IDENTITY_ATTRIBUTE] = _identity_payload(identity)
    npc = None
    try:
        created = spawn(materialized, caller=caller)
        if len(created) != 1:
            for obj in created:
                _delete_partial(obj)
            return MobileSpawnResult("failed", "unexpected_spawn_count")
        npc = created[0]
        if not _is_npc(npc):
            _delete_partial(npc)
            return MobileSpawnResult("failed", "non_npc_prototype")
        from systems.mobile_specials import (SpecialEvent, dispatch_specials,
                                             set_mobile_specials)
        from systems.mobiles import set_mobile_profile

        set_mobile_profile(npc, npc.attributes.get("mobile_behavior_profile", "idle"))
        set_mobile_specials(
            npc, npc.attributes.get("mobile_specials", {"version": 1, "behaviors": []})
        )
        if not npc.move_to(
            room, quiet=True, move_type="mobile_spawn", capacity_actor=npc
        ):
            _delete_partial(npc)
            return MobileSpawnResult("failed", "room_admission_denied")
        dispatch_specials(
            npc,
            SpecialEvent("reset" if identity.managed else "spawn", target=npc),
        )
    except Exception:
        if npc is not None:
            _delete_partial(npc)
        logger.log_trace(
            f"MOB-05 spawn failed for prototype {prototype_key} in room #{getattr(room, 'id', '?')}."
        )
        return MobileSpawnResult("failed", "spawn_failed")
    return MobileSpawnResult("created", npc=npc, created=1)


def mobile_population_snapshot(area_key: str) -> MobilePopulationSnapshot:
    """Count live NPCs without mutating state or relying on sessions/names."""
    _stable_key(area_key, "area_key")
    from typeclasses.characters import Character

    placement_counts: dict[str, int] = {}
    room_counts: dict[str, dict[str, int]] = {}
    area_counts: dict[str, int] = {}
    for npc in Character.objects.filter_family().iterator():
        if npc.attributes.get("is_player_character") is not False:
            continue
        try:
            identity = mobile_spawn_identity(npc)
        except MobileSpawnError:
            continue
        if identity is None:
            prototype_key = _prototype_tag_key(npc)
            if (
                prototype_key is None
                or area_of(getattr(npc, "location", None)) != area_key
            ):
                continue
            area_counts[prototype_key] = area_counts.get(prototype_key, 0) + 1
            current_room = room_key_of(npc.location)
            if current_room:
                _increment_room(room_counts, current_room, prototype_key)
            continue
        current_area = area_of(getattr(npc, "location", None))
        if identity.area_key == area_key:
            area_counts[identity.prototype_key] = (
                area_counts.get(identity.prototype_key, 0) + 1
            )
            if identity.placement_key:
                placement_counts[identity.placement_key] = (
                    placement_counts.get(identity.placement_key, 0) + 1
                )
        elif current_area == area_key:
            # Manual copies and managed copies that crossed an area boundary
            # do not satisfy a placement here, but they do use local capacity.
            area_counts[identity.prototype_key] = (
                area_counts.get(identity.prototype_key, 0) + 1
            )
        if current_area == area_key:
            current_room = room_key_of(npc.location)
            if current_room:
                _increment_room(room_counts, current_room, identity.prototype_key)
    return MobilePopulationSnapshot(
        area_key, placement_counts, room_counts, area_counts
    )


def reconcile_mobile_placement(
    reset_token: str, placement: Any, rooms: Mapping[str, Any]
) -> MobileSpawnResult:
    """Idempotently fill one placement without modifying surviving NPCs."""
    _stable_key(reset_token, "reset_token")
    placement = validate_mobile_placement(placement)
    room = rooms.get(placement.room_key)
    if room is None or area_of(room) != placement.area_key:
        return MobileSpawnResult("failed", "missing_reset_room")
    claim_key = f"{placement.area_key}:{reset_token}:{placement.placement_key}"
    with transaction.atomic():
        claims = _locked_claims()
        if claim_key in claims["claims"]:
            return MobileSpawnResult("duplicate", claims["claims"][claim_key]["status"])
        claims["claims"][claim_key] = {
            "status": "claimed",
            "created_ids": [],
            "reason": "",
        }
        _write_claims(claims)

    created = 0
    reason = ""
    while True:
        snapshot = mobile_population_snapshot(placement.area_key)
        managed = snapshot.placement_counts.get(placement.placement_key, 0)
        room_count = snapshot.room_prototype_counts.get(placement.room_key, {}).get(
            placement.prototype_key, 0
        )
        area_count = snapshot.area_prototype_counts.get(placement.prototype_key, 0)
        if managed >= placement.desired:
            break
        if room_count >= placement.room_max:
            reason = "room_maximum"
            break
        if area_count >= placement.area_max:
            reason = "area_maximum"
            break
        result = spawn_mobile(
            placement.prototype_key,
            room,
            identity=MobileSpawnIdentity(
                placement.prototype_key,
                placement.area_key,
                placement.area_key,
                placement.placement_key,
                placement.room_key,
            ),
        )
        if result.status != "created":
            reason = result.reason
            break
        created += 1
        _append_claim_created_id(claim_key, result.npc.id)
    _finish_claim(claim_key, created, reason)
    return MobileSpawnResult(
        "created" if created else "unchanged", reason, created=created
    )


def _identity_from_raw(raw: Any) -> MobileSpawnIdentity:
    expected = {
        "version",
        "prototype_key",
        "reset_owner",
        "area_key",
        "placement_key",
        "reset_room_key",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise MobileSpawnError("Mobile spawn identity has an invalid shape.")
    if raw["version"] != MOBILE_SPAWN_IDENTITY_VERSION:
        raise MobileSpawnError("Mobile spawn identity has an unsupported version.")
    identity = MobileSpawnIdentity(
        raw["prototype_key"],
        raw["reset_owner"],
        raw["area_key"],
        raw["placement_key"],
        raw["reset_room_key"],
    )
    _stable_key(identity.prototype_key, "prototype_key")
    optional = (
        identity.reset_owner,
        identity.area_key,
        identity.placement_key,
        identity.reset_room_key,
    )
    if any(value is not None and not isinstance(value, str) for value in optional):
        raise MobileSpawnError("Mobile spawn identity contains an invalid key.")
    if any(value is not None for value in optional):
        if not all(value is not None for value in optional):
            raise MobileSpawnError("Managed mobile identity is incomplete.")
        for value in optional:
            _stable_key(value, "managed identity key")
    return identity


def _identity_payload(identity: MobileSpawnIdentity) -> dict[str, Any]:
    payload = {
        "version": MOBILE_SPAWN_IDENTITY_VERSION,
        "prototype_key": identity.prototype_key,
        "reset_owner": identity.reset_owner,
        "area_key": identity.area_key,
        "placement_key": identity.placement_key,
        "reset_room_key": identity.reset_room_key,
    }
    _identity_from_raw(payload)
    return payload


def _prototype_for_key(key: str) -> dict[str, Any]:
    _stable_key(key, "prototype_key")
    matches = [
        proto for proto in search_prototype(key) if proto.get("prototype_key") == key
    ]
    if len(matches) != 1:
        raise MobileSpawnError("NPC prototype is missing or ambiguous.")
    prototype = matches[0]
    if prototype.get("typeclass") != NPC_TYPECLASS:
        raise MobileSpawnError("Mobile prototype is not an NPC Character prototype.")
    return deepcopy(prototype)


def _flatten_prototype(prototype: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: deepcopy(value) for key, value in prototype.items() if key != "attrs"
    }
    for entry in prototype.get("attrs", []):
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise MobileSpawnError("NPC prototype has malformed attributes.")
        result[entry[0]] = deepcopy(entry[1])
    return result


def _is_room(room: Any) -> bool:
    return room is not None and inherits_from(
        room, "evennia.objects.objects.DefaultRoom"
    )


def _is_npc(obj: Any) -> bool:
    return obj is not None and inherits_from(obj, NPC_TYPECLASS)


def _validate_classed_npc(prototype: Mapping[str, Any]) -> None:
    """Reject an explicit NPC class unless its whole kit is P-05-published.

    NPCs with no ``char_class`` are intentionally classless templates.  That
    keeps ordinary world mobiles authorable while the release manifest is
    empty, and prevents an authored source-table name from becoming a hidden
    class implementation at spawn time.
    """
    class_key = prototype.get("char_class")
    if class_key in (None, ""):
        return
    level = prototype.get("level", 1)
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= 20
    ):
        raise MobileSpawnError("NPC class data is invalid.")
    try:
        CLASS_PROGRESSION.class_for(class_key)
    except RegistryValidationError as err:
        raise MobileSpawnError("NPC class data is invalid.") from err
    if not is_level_published(class_key, level):
        raise MobileSpawnError("NPC class is not published for that level.")


def _stable_key(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise MobileSpawnError(f"Mobile {label} must be a stable key.")
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in value):
        raise MobileSpawnError(
            f"Mobile {label} must be lowercase letters, numbers, _ or -."
        )


def _prototype_tag_key(npc: Any) -> str | None:
    from evennia.prototypes.prototypes import PROTOTYPE_TAG_CATEGORY

    keys = npc.tags.get(category=PROTOTYPE_TAG_CATEGORY, return_list=True)
    if len(keys) != 1:
        return None
    try:
        _stable_key(keys[0], "prototype_key")
        _prototype_for_key(keys[0])
    except MobileSpawnError:
        return None
    return keys[0]


def _increment_room(
    counts: dict[str, dict[str, int]], room_key: str, prototype_key: str
) -> None:
    by_prototype = counts.setdefault(room_key, {})
    by_prototype[prototype_key] = by_prototype.get(prototype_key, 0) + 1


def _delete_partial(obj: Any) -> None:
    for child in tuple(getattr(obj, "contents", ())):
        _delete_partial(child)
    try:
        obj.delete()
    except Exception:
        logger.log_trace(
            f"MOB-05 could not clean partial object #{getattr(obj, 'id', '?')}."
        )


def _initial_claims() -> dict[str, Any]:
    return {"version": MOBILE_SPAWN_CLAIMS_VERSION, "claims": {}}


def _locked_claims() -> dict[str, Any]:
    config, _ = ServerConfig.objects.select_for_update().get_or_create(
        db_key=MOBILE_SPAWN_CLAIMS_CONFIG_KEY, defaults={"db_value": _initial_claims()}
    )
    raw = config.value
    if (
        not isinstance(raw, Mapping)
        or raw.get("version") != MOBILE_SPAWN_CLAIMS_VERSION
        or not isinstance(raw.get("claims"), Mapping)
    ):
        raise MobileSpawnError("Mobile spawn claims are malformed.")
    return {
        "version": MOBILE_SPAWN_CLAIMS_VERSION,
        "claims": deepcopy(dict(raw["claims"])),
    }


def _write_claims(claims: dict[str, Any]) -> None:
    ServerConfig.objects.conf(MOBILE_SPAWN_CLAIMS_CONFIG_KEY, value=claims)


def _append_claim_created_id(claim_key: str, object_id: int) -> None:
    with transaction.atomic():
        claims = _locked_claims()
        claims["claims"][claim_key]["created_ids"].append(object_id)
        _write_claims(claims)


def _finish_claim(claim_key: str, created: int, reason: str) -> None:
    with transaction.atomic():
        claims = _locked_claims()
        claim = claims["claims"][claim_key]
        claim["status"] = "complete" if not reason else "failed"
        claim["reason"] = reason
        claim["created"] = created
        _write_claims(claims)

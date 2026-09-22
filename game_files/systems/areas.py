"""
Area export / import — the "build live, save to git" half of the builder
workflow.

Builders create rooms live in the running world (fast, immediate).  When an area
is ready, ``export_area`` snapshots every room tagged into that area into a
readable, version-controllable module under ``game_files/world/areas/<area>.py``.

The hard part is the exit graph.  ``prototype_from_object`` captures
``location``/``destination`` as **dbrefs**, which are meaningless in a freshly
built world.  So exits are not stored as raw prototypes; they are stored as
``(from_key, direction, to_key, attrs)`` tuples that reference *room keys*.
``load_area`` then does the matching two-pass import: spawn the rooms first,
build a ``key -> room`` map, then create the exits resolved through that map.

Rooms carry two bookkeeping tags so this round-trips idempotently:

* category ``area``      — which area the room belongs to (one per room), and
* category ``room_key``  — the room's stable per-area key (DEKU's take on the
  DIKU vnum), unique within the area.
"""

import ast
import hashlib
import importlib.util
import json
import math
import os
import pkgutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pprint import pformat
from types import MappingProxyType
from typing import Any

from django.conf import settings
from evennia import create_object
from evennia.prototypes.prototypes import search_prototype
from evennia.prototypes.spawner import prototype_from_object, spawn
from evennia.utils import logger
from evennia.utils.search import search_tag
from systems.doors import (
    DoorError,
    apply_door_area_data,
    door_area_data,
    validate_area_exit_doors,
    validate_door_area_data,
)
from systems.room_environment import (
    ROOM_ENVIRONMENT_ATTRIBUTE,
    ROOM_ENVIRONMENT_VERSION,
    RoomEnvironmentError,
    room_environment_data,
    validate_room_environment,
)
from systems.room_policy import (
    ROOM_POLICY_ATTRIBUTE,
    ROOM_POLICY_VERSION,
    RoomPolicyError,
    room_policy_data,
    validate_room_policy,
)
from systems.travel import SECTOR_ATTRIBUTE, SECTORS, sector_key
from systems.visibility import validate_extra_descriptions
from world.build_schema import as_slug

AREA_TAG_CATEGORY = "area"
ROOM_KEY_CATEGORY = "room_key"
EXIT_KEY_CATEGORY = "exit_key"
EXTERNAL_DESTINATION_ATTRIBUTE = "external_destination"

# AREA-01A is deliberately a small envelope around the legacy room graph.  The
# later AREA-01 packages own the detailed records, but every tracked area now
# has one safe, versioned boundary rather than a collection of module globals.
MANIFEST_VERSION = 1
MANIFEST_FIELDS = frozenset(
    {
        "key",
        "display_name",
        "schema_version",
        "dependencies",
        "credits",
        "srd_references",
        "reset_policy",
        "lifespan_pulses",
        "rooms",
        "exits",
        "mobiles",
        "objects",
    }
)
MAX_MANIFEST_TEXT_LENGTH = 2_000
MAX_MANIFEST_COLLECTION_SIZE = 2_000
MAX_MANIFEST_DEPTH = 20
MAX_LIFESPAN_PULSES = 1_000_000
MAX_OBJECT_CONTENT_DEPTH = 10
MAX_OBJECT_CHILDREN = 100
MAX_OBJECT_QUANTITY = 100


class AreaManifestError(ValueError):
    """A tracked area manifest is malformed, unsafe, or unsupported."""


class AreaRegistryError(AreaManifestError):
    """The enabled area's source records cannot form one unambiguous world."""


class AreaPlanError(AreaRegistryError):
    """A complete source world could not be compiled into a load plan."""


@dataclass(frozen=True)
class AreaRegistry:
    """Read-only, whole-world source index compiled before loader mutation.

    ``manifests`` and ``prototypes`` are keyed only by their stable source
    identities.  In particular, this registry deliberately has no dbrefs,
    display-name index, or fuzzy lookup fallback.
    """

    manifests: Mapping[str, Mapping[str, Any]]
    rooms: Mapping[str, Mapping[str, Any]]
    prototypes: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class AreaLoadPlan:
    """Immutable, ordered source work for a future world apply transaction."""

    registry: AreaRegistry
    source_prototypes: tuple[tuple[str, Mapping[str, Any]], ...]
    rooms: tuple[tuple[str, Mapping[str, Any]], ...]
    exits_and_doors: tuple[tuple[str, str, Mapping[str, Any]], ...]
    service_assignments: tuple[tuple[str, str, Mapping[str, Any]], ...]
    mobile_placements: tuple[tuple[str, Mapping[str, Any]], ...]
    object_placements: tuple[tuple[str, str, Mapping[str, Any]], ...]

    @property
    def stages(self) -> tuple[str, ...]:
        """Return the mandatory materialization order for consumers."""
        return (
            "source_prototypes",
            "rooms",
            "exits_and_doors",
            "service_assignments",
            "mobile_placements",
            "object_placements",
        )


def canonical_room_reference(area_key: object, room_key: object) -> str:
    """Return the sole persisted spelling for a managed room reference."""
    return f"{_slug(area_key, 'area key')}:{_slug(room_key, 'room key')}"


def normalize_room_reference(reference: object, current_area: object) -> str:
    """Normalize a local or ``area:room`` reference without searching live data."""
    if not isinstance(reference, str) or not reference:
        raise AreaManifestError("Room reference must be a room key or area:room.")
    parts = reference.split(":")
    if len(parts) == 1:
        return canonical_room_reference(current_area, parts[0])
    if len(parts) == 2 and all(parts):
        return canonical_room_reference(parts[0], parts[1])
    raise AreaManifestError(
        "Room reference must contain exactly one area:room separator."
    )


def _normalize_data(value: Any) -> Any:
    """Return a JSON-like copy, rejecting live objects and executable values."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AreaManifestError("Manifest numbers must be finite.")
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_normalize_data(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise AreaManifestError("Manifest mapping keys must be strings.")
        return {key: _normalize_data(item) for key, item in value.items()}
    raise AreaManifestError(
        f"Manifest data may contain only primitive values, not {type(value).__name__}."
    )


def _check_bounds(value: Any, depth: int = 0) -> None:
    """Enforce bounded text, nesting, and collections on normalized data."""
    if depth > MAX_MANIFEST_DEPTH:
        raise AreaManifestError("Manifest nesting exceeds its maximum depth.")
    if isinstance(value, str):
        if len(value) > MAX_MANIFEST_TEXT_LENGTH:
            raise AreaManifestError("Manifest text exceeds its maximum length.")
    elif isinstance(value, list):
        if len(value) > MAX_MANIFEST_COLLECTION_SIZE:
            raise AreaManifestError("Manifest collection exceeds its maximum size.")
        for item in value:
            _check_bounds(item, depth + 1)
    elif isinstance(value, dict):
        if len(value) > MAX_MANIFEST_COLLECTION_SIZE:
            raise AreaManifestError("Manifest collection exceeds its maximum size.")
        for key, item in value.items():
            _check_bounds(key, depth + 1)
            _check_bounds(item, depth + 1)


def _slug(value: object, field: str) -> str:
    """Validate one existing-style slug without silently changing authored data."""
    if not isinstance(value, str):
        raise AreaManifestError(f"Manifest {field} must be a slug.")
    try:
        if as_slug(value) != value:
            raise AreaManifestError(f"Manifest {field} must be a normalized slug.")
    except ValueError as err:
        raise AreaManifestError(f"Manifest {field} must be a slug.") from err
    return value


def validate_area_manifest(
    manifest: object, *, validate_runtime_prototypes: bool = True
) -> dict[str, Any]:
    """Validate and normalize a version-one, data-only area manifest.

    Whole-world source planning passes ``False`` so it never reads database
    prototypes; AREA-05A's catalog registry supplies that validation instead.
    """
    try:
        data = _normalize_data(manifest)
    except AreaManifestError:
        raise
    if not isinstance(data, dict):
        raise AreaManifestError("Manifest must be a mapping.")
    # AREA-01A/B manifests predate object placements. Their empty default is
    # deliberately migration-compatible rather than an implicit live reset.
    data.setdefault("objects", {})
    unknown = set(data) - MANIFEST_FIELDS
    missing = MANIFEST_FIELDS - set(data)
    if unknown:
        raise AreaManifestError(
            f"Manifest has unknown fields: {', '.join(sorted(unknown))}."
        )
    if missing:
        raise AreaManifestError(
            f"Manifest is missing fields: {', '.join(sorted(missing))}."
        )
    _check_bounds(data)
    _slug(data["key"], "key")
    if not isinstance(data["display_name"], str) or not data["display_name"].strip():
        raise AreaManifestError("Manifest display_name must be non-empty text.")
    if data["schema_version"] != MANIFEST_VERSION:
        raise AreaManifestError(
            f"Unsupported area manifest version: {data['schema_version']!r}."
        )
    for field in ("dependencies",):
        if not isinstance(data[field], list) or len(set(data[field])) != len(
            data[field]
        ):
            raise AreaManifestError(f"Manifest {field} must be a unique list of slugs.")
        for value in data[field]:
            _slug(value, field)
    for field in ("credits", "srd_references"):
        if not isinstance(data[field], list) or not all(
            isinstance(value, str) and value.strip() for value in data[field]
        ):
            raise AreaManifestError(
                f"Manifest {field} must be a list of non-empty text."
            )
    _slug(data["reset_policy"], "reset_policy")
    if (
        not isinstance(data["lifespan_pulses"], int)
        or isinstance(data["lifespan_pulses"], bool)
        or not 0 <= data["lifespan_pulses"] <= MAX_LIFESPAN_PULSES
    ):
        raise AreaManifestError(
            "Manifest lifespan_pulses is outside its supported range."
        )
    if not isinstance(data["rooms"], dict) or not isinstance(
        data["exits"], (list, dict)
    ):
        raise AreaManifestError("Manifest rooms and exits have invalid section types.")
    if not isinstance(data["mobiles"], list):
        raise AreaManifestError("Manifest mobiles has an invalid section type.")
    if not isinstance(data["objects"], dict):
        raise AreaManifestError("Manifest objects has an invalid section type.")
    _validate_area_records(
        data, validate_runtime_prototypes=validate_runtime_prototypes
    )
    return data


def manifest_fingerprint(manifest: object) -> str:
    """Return the SHA-256 fingerprint of validated, canonical manifest data."""
    data = validate_area_manifest(manifest)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_area_manifest(area_slug: str) -> dict[str, Any]:
    """Snapshot a live area in AREA-01A's versioned envelope."""
    area_slug = _slug(area_slug, "key")
    rooms, exits = _manifest_room_records(area_slug), _manifest_exit_records(area_slug)
    return validate_area_manifest(
        {
            "key": area_slug,
            "display_name": area_slug.replace("_", " ").title(),
            "schema_version": MANIFEST_VERSION,
            "dependencies": [],
            "credits": [],
            "srd_references": [],
            "reset_policy": "default",
            "lifespan_pulses": 0,
            "rooms": rooms,
            "exits": exits,
            "mobiles": [],
            "objects": {},
        }
    )


# Attributes of an Evennia prototype that are tied to a specific live object and
# must be dropped before the prototype can be re-spawned in another world.
_UNSTABLE_PROTOTYPE_KEYS = (
    "location",
    "home",
    "destination",
    "prototype_desc",
    "prototype_locks",
    "prototype_tags",
)


# ---------------------------------------------------------------------------
# Area / room-key tagging (shared with the build command)
# ---------------------------------------------------------------------------


def room_key_of(room) -> str | None:
    """Return the room's stable per-area key tag, or ``None`` if unset."""
    keys = room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True)
    return keys[0] if keys else None


def exit_key_of(exit_obj) -> str | None:
    """Return an exit's stable manifest-record key, or ``None`` if unset."""
    keys = exit_obj.tags.get(category=EXIT_KEY_CATEGORY, return_list=True)
    return keys[0] if keys else None


def area_of(room) -> str | None:
    """Return the room's area slug tag, or ``None`` if it has no area."""
    areas = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    return areas[0] if areas else None


def _room_key_taken(area_slug: str, room_key: str, exclude) -> bool:
    """True if another room in ``area_slug`` already uses ``room_key``."""
    for other in search_tag(room_key, category=ROOM_KEY_CATEGORY):
        if other != exclude and other.tags.has(area_slug, category=AREA_TAG_CATEGORY):
            return True
    return False


def ensure_room_key(room, area_slug: str) -> str:
    """Return the room's per-area key, assigning a unique one if it has none.

    The key is derived from the room's name; collisions within the same area get
    a numeric suffix so two rooms can never silently collapse on export.
    """
    existing = room_key_of(room)
    if existing:
        return existing
    base = as_slug(room.key)
    key, suffix = base, 2
    while _room_key_taken(area_slug, key, room):
        key, suffix = f"{base}_{suffix}", suffix + 1
    room.tags.add(key, category=ROOM_KEY_CATEGORY)
    return key


def ensure_exit_key(exit_obj, area_slug: str, source_key: str) -> str:
    """Return a stable per-area exit key without deriving it from mutable text."""
    existing = exit_key_of(exit_obj)
    if existing:
        return existing
    base = f"{source_key}_{as_slug(exit_obj.key)}"
    key, suffix = base, 2
    while any(
        other != exit_obj for other in search_tag(key, category=EXIT_KEY_CATEGORY)
    ):
        key, suffix = f"{base}_{suffix}", suffix + 1
    exit_obj.tags.add(key, category=EXIT_KEY_CATEGORY)
    return key


def external_destination_data(exit_obj) -> dict[str, str] | None:
    """Read a validated explicit external destination without leaking dbrefs."""
    raw = exit_obj.attributes.get(EXTERNAL_DESTINATION_ATTRIBUTE)
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"area_key", "room_key"}:
        raise AreaManifestError("External destination has an invalid schema.")
    return {
        "area_key": _slug(raw["area_key"], "external destination area_key"),
        "room_key": _slug(raw["room_key"], "external destination room_key"),
    }


def set_external_destination(exit_obj, area_key: str, room_key: str) -> None:
    """Persist the only supported cross-area reference for one live exit."""
    exit_obj.attributes.add(
        EXTERNAL_DESTINATION_ATTRIBUTE,
        {
            "area_key": _slug(area_key, "external destination area_key"),
            "room_key": _slug(room_key, "external destination room_key"),
        },
    )


def assign_area(room, area_slug: str) -> None:
    """Tag ``room`` into ``area_slug`` (replacing any prior area) and key it."""
    profiles = {
        profile
        for member in search_tag(area_slug, category=AREA_TAG_CATEGORY)
        for profile in member.tags.get(category="weather_profile", return_list=True)
    }
    for old in room.tags.get(category=AREA_TAG_CATEGORY, return_list=True):
        room.tags.remove(old, category=AREA_TAG_CATEGORY)
    room.tags.add(area_slug, category=AREA_TAG_CATEGORY)
    if profiles == {"temperate"}:
        room.tags.add("temperate", category="weather_profile")
    ensure_room_key(room, area_slug)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def rooms_in_area(area_slug: str) -> list:
    """Return the rooms tagged into ``area_slug``."""
    return list(search_tag(area_slug, category=AREA_TAG_CATEGORY))


def area_index() -> dict[str, list]:
    """Return ``{area_slug: [rooms, ...]}`` for every area, in one tag query.

    ``search_tag`` with no key returns all objects carrying *any* tag in the
    category, so this groups the whole built world by area without scanning
    every room typeclass.
    """
    index: dict[str, list] = {}
    for room in search_tag(category=AREA_TAG_CATEGORY):
        for area in room.tags.get(category=AREA_TAG_CATEGORY, return_list=True):
            index.setdefault(area, []).append(room)
    return index


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _room_prototype(room, area_slug: str, room_key: str) -> dict:
    """Build a re-spawnable prototype dict for a single room."""
    prot = prototype_from_object(room)
    for key in _UNSTABLE_PROTOTYPE_KEYS:
        prot.pop(key, None)
    prot["prototype_key"] = f"{area_slug}_{room_key}"
    # The area/room_key tags are re-applied by the loader; don't bake them in.
    if "tags" in prot:
        prot["tags"] = [
            tag
            for tag in prot["tags"]
            if tag[1] not in (AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY)
        ]
        if not prot["tags"]:
            prot.pop("tags")
    policy = room_policy_data(room)
    environment = room_environment_data(room)
    sector = sector_key(room)
    if sector is None:
        raise ValueError(f"Room '{room.key}' has an invalid sector.")
    attrs = [
        attr
        for attr in prot.get("attrs", [])
        if attr[0] not in {ROOM_POLICY_ATTRIBUTE, ROOM_ENVIRONMENT_ATTRIBUTE}
    ]
    attrs.append((ROOM_POLICY_ATTRIBUTE, policy, None, ""))
    attrs.append((ROOM_ENVIRONMENT_ATTRIBUTE, environment, None, ""))
    attrs = [attr for attr in attrs if attr[0] != SECTOR_ATTRIBUTE]
    attrs.append((SECTOR_ATTRIBUTE, sector, None, ""))
    prot["attrs"] = attrs
    return prot


def build_area_data(area_slug: str) -> tuple[dict, list]:
    """Snapshot a live area into serialisable ``(rooms, exits)`` structures.

    This is the pure core of the exporter (no filesystem), so it can be unit
    tested and reused by the loader's round-trip tests.
    """
    rooms_objs = list(search_tag(area_slug, category=AREA_TAG_CATEGORY))

    # Resolve each room's key up front so exits can reference rooms by key.
    key_by_id: dict[int, str] = {}
    for room in rooms_objs:
        key_by_id[room.id] = room_key_of(room) or ensure_room_key(room, area_slug)

    rooms = {
        key_by_id[room.id]: _room_prototype(room, area_slug, key_by_id[room.id])
        for room in rooms_objs
    }

    exits = []
    for room in rooms_objs:
        from_key = key_by_id[room.id]
        for ex in room.exits:
            dest = ex.destination
            if dest is None or dest.id not in key_by_id:
                continue  # exit leaves the area — out of scope for this file
            attrs = {}
            aliases = ex.aliases.all()
            if aliases:
                attrs["aliases"] = sorted(aliases)
            door = door_area_data(ex)
            if door is not None:
                attrs["door_state"] = door
            exits.append((from_key, ex.key, key_by_id[dest.id], attrs))

    return rooms, sorted(exits)


def _manifest_room_records(area_slug: str) -> dict[str, dict[str, Any]]:
    """Export explicit AREA-01B room records, excluding all runtime state."""
    records: dict[str, dict[str, Any]] = {}
    for room in search_tag(area_slug, category=AREA_TAG_CATEGORY):
        key = room_key_of(room) or ensure_room_key(room, area_slug)
        profiles = room.tags.get(category="weather_profile", return_list=True)
        if len(profiles) > 1 or any(profile != "temperate" for profile in profiles):
            raise AreaManifestError(f"Room '{key}' has an invalid weather profile.")
        records[key] = {
            "name": room.key,
            "description": room.attributes.get("desc") or "",
            "extra_descriptions": room.attributes.get("extra_descs") or [],
            "sector": sector_key(room),
            "policy": room_policy_data(room),
            "environment": room_environment_data(room),
            "weather_profile": profiles[0] if profiles else None,
        }
    return dict(sorted(records.items()))


def _manifest_exit_records(area_slug: str) -> dict[str, dict[str, Any]]:
    """Export explicit exit records and only explicit cross-area references."""
    rooms = list(search_tag(area_slug, category=AREA_TAG_CATEGORY))
    key_by_id = {
        room.id: room_key_of(room) or ensure_room_key(room, area_slug) for room in rooms
    }
    records: dict[str, dict[str, Any]] = {}
    for room in rooms:
        source_key = key_by_id[room.id]
        for exit_obj in room.exits:
            destination = None
            if (
                exit_obj.destination is not None
                and exit_obj.destination.id in key_by_id
            ):
                destination = {
                    "kind": "local",
                    "room_key": key_by_id[exit_obj.destination.id],
                }
            else:
                external = external_destination_data(exit_obj)
                if external is None:
                    continue
                destination = {"kind": "external", **external}
            record_key = ensure_exit_key(exit_obj, area_slug, source_key)
            records[record_key] = {
                "source_room": source_key,
                "name": exit_obj.key,
                "description": exit_obj.attributes.get("desc") or "",
                "aliases": sorted(exit_obj.aliases.all()),
                "destination": destination,
                "door": door_area_data(exit_obj),
            }
    return dict(sorted(records.items()))


def _validate_area_records(
    data: dict[str, Any], *, validate_runtime_prototypes: bool = True
) -> None:
    """Validate AREA-01B room/exit records before any live-world mutation."""
    rooms, exits = data["rooms"], data["exits"]
    # AREA-01A's legacy-shaped sections remain loadable until re-exported.
    if isinstance(exits, list):
        return
    if not isinstance(exits, dict):
        raise AreaManifestError("Manifest exits must be a mapping or legacy list.")
    for key, room in rooms.items():
        _slug(key, "room key")
        if not isinstance(room, dict) or set(room) != {
            "name",
            "description",
            "extra_descriptions",
            "sector",
            "policy",
            "environment",
            "weather_profile",
        }:
            raise AreaManifestError(f"Room '{key}' has an invalid record schema.")
        if not isinstance(room["name"], str) or not room["name"].strip():
            raise AreaManifestError(f"Room '{key}' needs a name.")
        if not isinstance(room["description"], str):
            raise AreaManifestError(f"Room '{key}' description must be text.")
        try:
            validate_extra_descriptions(room["extra_descriptions"])
            if room["sector"] not in SECTORS:
                raise ValueError("invalid sector")
            validate_room_policy(room["policy"])
            validate_room_environment(room["environment"])
        except (ValueError, RoomPolicyError, RoomEnvironmentError) as err:
            raise AreaManifestError(f"Room '{key}' has invalid authored data.") from err
        if room["weather_profile"] not in (None, "temperate"):
            raise AreaManifestError(f"Room '{key}' has an invalid weather profile.")
    pair_entries = []
    for key, exit_record in exits.items():
        _slug(key, "exit key")
        if not isinstance(exit_record, dict) or set(exit_record) != {
            "source_room",
            "name",
            "description",
            "aliases",
            "destination",
            "door",
        }:
            raise AreaManifestError(f"Exit '{key}' has an invalid record schema.")
        if exit_record["source_room"] not in rooms:
            raise AreaManifestError(f"Exit '{key}' has an unknown source room.")
        if not isinstance(exit_record["name"], str) or not exit_record["name"].strip():
            raise AreaManifestError(f"Exit '{key}' needs a name.")
        if (
            not isinstance(exit_record["description"], str)
            or not isinstance(exit_record["aliases"], list)
            or not all(
                isinstance(alias, str) and alias for alias in exit_record["aliases"]
            )
        ):
            raise AreaManifestError(f"Exit '{key}' has invalid display data.")
        destination = exit_record["destination"]
        if not isinstance(destination, dict) or destination.get("kind") not in {
            "local",
            "external",
        }:
            raise AreaManifestError(f"Exit '{key}' has an invalid destination.")
        if destination["kind"] == "local":
            if (
                set(destination) != {"kind", "room_key"}
                or destination["room_key"] not in rooms
            ):
                raise AreaManifestError(
                    f"Exit '{key}' has an unknown local destination."
                )
        elif set(destination) != {"kind", "area_key", "room_key"}:
            raise AreaManifestError(
                f"Exit '{key}' has an invalid external destination."
            )
        else:
            _slug(destination["area_key"], "external area key")
            _slug(destination["room_key"], "external room key")
        if exit_record["door"] is not None:
            try:
                validate_door_area_data(exit_record["door"])
            except DoorError as err:
                raise AreaManifestError(f"Exit '{key}' has invalid door data.") from err
            if destination["kind"] == "local":
                pair_entries.append(
                    (
                        exit_record["source_room"],
                        exit_record["name"],
                        destination["room_key"],
                        {"door_state": exit_record["door"]},
                    )
                )
    try:
        validate_area_exit_doors(pair_entries)
    except DoorError as err:
        raise AreaManifestError("Manifest has invalid paired doors.") from err
    _validate_manifest_mobile_placements(
        data, validate_runtime_prototypes=validate_runtime_prototypes
    )
    _validate_manifest_object_placements(
        data, validate_runtime_prototypes=validate_runtime_prototypes
    )


def _validate_manifest_mobile_placements(
    data: dict[str, Any], *, validate_runtime_prototypes: bool
) -> None:
    """Keep AREA-01C mobile records exactly on MOB-05's placement contract."""
    seen: set[str] = set()
    for placement in data["mobiles"]:
        if validate_runtime_prototypes:
            from systems.mob_spawning import MobileSpawnError, validate_mobile_placement

            try:
                validated = validate_mobile_placement(placement, area_key=data["key"])
            except MobileSpawnError as err:
                raise AreaManifestError(
                    "Manifest has an invalid mobile placement."
                ) from err
            placement_key, room_key, prototype_key = (
                validated.placement_key,
                validated.room_key,
                validated.prototype_key,
            )
        else:
            expected = {
                "area_key",
                "room_key",
                "placement_key",
                "prototype_key",
                "desired",
                "room_max",
                "area_max",
            }
            if not isinstance(placement, dict) or set(placement) != expected:
                raise AreaManifestError("Manifest has an invalid mobile placement.")
            try:
                if placement["area_key"] != data["key"]:
                    raise ValueError
                room_key = _slug(placement["room_key"], "mobile room key")
                placement_key = _slug(
                    placement["placement_key"], "mobile placement key"
                )
                prototype_key = _slug(
                    placement["prototype_key"], "mobile prototype key"
                )
                limits = (placement["desired"], placement["room_max"], placement["area_max"])
                if (
                    any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in limits)
                    or limits[0] > limits[1]
                    or limits[0] > limits[2]
                ):
                    raise ValueError
            except (AreaManifestError, ValueError) as err:
                raise AreaManifestError(
                    "Manifest has an invalid mobile placement."
                ) from err
        if placement_key in seen or room_key not in data["rooms"]:
            raise AreaManifestError(
                "Manifest mobile placements need unique known rooms."
            )
        seen.add(placement_key)
        if validate_runtime_prototypes:
            _validate_mobile_prototype_services(prototype_key)


def _prototype_for_kind(
    prototype_key: str, typeclass: str, label: str
) -> dict[str, Any]:
    """Return one exact source prototype of the required non-executable kind."""
    _slug(prototype_key, f"{label} prototype key")
    matches = [
        prototype
        for prototype in search_prototype(prototype_key)
        if prototype.get("prototype_key") == prototype_key
    ]
    if len(matches) != 1 or matches[0].get("typeclass") != typeclass:
        raise AreaManifestError(
            f"{label.title()} prototype is missing, ambiguous, or wrong-kind."
        )
    return matches[0]


def _prototype_attributes(prototype: dict[str, Any]) -> dict[str, Any]:
    """Read only literal prototype attributes for service validation."""
    # Evennia accepts both direct prototype keys and its serialized ``attrs``
    # form; builders may have authored either, so neither can bypass checks.
    attributes = {
        key: prototype[key]
        for key in (
            "mobile_policy",
            "mobile_specials",
            "mobile_behavior_profile",
            "trainer_profile",
        )
        if key in prototype
    }
    for entry in prototype.get("attrs", []):
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise AreaManifestError("Prototype attributes are malformed.")
        attributes[entry[0]] = entry[1]
    return attributes


def _validate_mobile_prototype_services(prototype_key: str) -> None:
    """Validate services on an NPC prototype without granting area-side behavior."""
    from systems.mob_spawning import NPC_TYPECLASS
    from systems.mobile_policy import default_mobile_policy, validate_mobile_policy
    from systems.mobile_specials import (
        default_mobile_specials,
        validate_mobile_specials,
    )
    from systems.mobiles import initial_mobile_state
    from systems.training import validate_trainer_profile

    prototype = _prototype_for_kind(prototype_key, NPC_TYPECLASS, "mobile")
    attributes = _prototype_attributes(prototype)
    try:
        validate_mobile_policy(attributes.get("mobile_policy", default_mobile_policy()))
        validate_mobile_specials(
            attributes.get("mobile_specials", default_mobile_specials())
        )
        initial_mobile_state(attributes.get("mobile_behavior_profile", "idle"))
        if attributes.get("trainer_profile") is not None:
            validate_trainer_profile(attributes["trainer_profile"])
    except ValueError as err:
        raise AreaManifestError("Mobile prototype has invalid service data.") from err


def _validate_manifest_object_placements(
    data: dict[str, Any], *, validate_runtime_prototypes: bool
) -> None:
    """Validate bounded, rooted item trees without materializing any objects."""
    seen: set[str] = set()
    for placement_key, placement in data["objects"].items():
        _slug(placement_key, "object placement key")
        if placement_key in seen:
            raise AreaManifestError("Object placement keys must be unique.")
        seen.add(placement_key)
        if not isinstance(placement, dict) or set(placement) != {
            "room_key",
            "prototype_key",
            "desired",
            "room_max",
            "area_max",
            "contents",
        }:
            raise AreaManifestError(
                f"Object placement '{placement_key}' has an invalid schema."
            )
        if placement["room_key"] not in data["rooms"]:
            raise AreaManifestError(
                f"Object placement '{placement_key}' has an unknown room."
            )
        for field in ("desired", "room_max", "area_max"):
            value = placement[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_OBJECT_QUANTITY
            ):
                raise AreaManifestError(
                    f"Object placement '{placement_key}' has an invalid {field}."
                )
        if (
            placement["desired"] > placement["room_max"]
            or placement["desired"] > placement["area_max"]
        ):
            raise AreaManifestError(
                f"Object placement '{placement_key}' exceeds a ceiling."
            )
        if validate_runtime_prototypes:
            _prototype_for_kind(
                placement["prototype_key"], settings.BASE_OBJECT_TYPECLASS, "item"
            )
        contents = placement["contents"]
        if not isinstance(contents, list) or len(contents) > MAX_OBJECT_CHILDREN:
            raise AreaManifestError("Object contents must be a bounded list.")
        for child in contents:
            _validate_object_node(
                child,
                ancestors=(placement["prototype_key"],),
                validate_runtime_prototypes=validate_runtime_prototypes,
            )


def _validate_object_node(
    node: Any, *, ancestors: tuple[str, ...], validate_runtime_prototypes: bool
) -> None:
    """Validate one nested authored item node and prohibit recursive prototypes."""
    if not isinstance(node, dict) or set(node) != {
        "prototype_key",
        "quantity",
        "contents",
    }:
        raise AreaManifestError("Object contents have an invalid schema.")
    prototype_key, quantity = node["prototype_key"], node["quantity"]
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or not 1 <= quantity <= MAX_OBJECT_QUANTITY
    ):
        raise AreaManifestError(
            "Object content quantity is outside its supported range."
        )
    if prototype_key in ancestors or len(ancestors) >= MAX_OBJECT_CONTENT_DEPTH:
        raise AreaManifestError(
            "Object contents contain a cycle or exceed nesting depth."
        )
    if validate_runtime_prototypes:
        _prototype_for_kind(prototype_key, settings.BASE_OBJECT_TYPECLASS, "item")
    children = node["contents"]
    if not isinstance(children, list) or len(children) > MAX_OBJECT_CHILDREN:
        raise AreaManifestError("Object contents must be a bounded list.")
    for child in children:
        _validate_object_node(
            child,
            ancestors=(*ancestors, prototype_key),
            validate_runtime_prototypes=validate_runtime_prototypes,
        )


def _areas_dir() -> str:
    """Absolute path to the git-tracked ``game_files/world/areas`` directory.

    At runtime this module lives at ``<repo>/game/systems/areas.py``; the source
    of truth is the sibling ``<repo>/game_files/world/areas``.
    """
    game_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(game_dir)
    return os.path.join(repo_root, "game_files", "world", "areas")


def _render_manifest(manifest: object) -> str:
    """Render a stable, literal-only module for one validated manifest."""
    data = validate_area_manifest(manifest)
    return (
        '"""Generated AREA-01A data-only area manifest.  Do not add code.\n"""\n\n'
        f"MANIFEST = {pformat(data, width=88, sort_dicts=True)}\n"
    )


def _render_module(
    area_slug: str, rooms: dict, exits: list, mobiles: list | None = None
) -> str:
    """Render a legacy module; retained only for migration-reader tests."""
    header = (
        f'"""Area "{area_slug}" — generated by the build command.\n\n'
        "Edit in-game and re-export, or hand-edit and reload; both round-trip\n"
        'through systems.areas.load_area("%s").\n"""\n\n' % area_slug
    )
    rooms_src = pformat(rooms, width=88, sort_dicts=True)
    exits_src = pformat(exits, width=88)
    mobiles_src = pformat(mobiles or [], width=88, sort_dicts=True)
    return f"{header}ROOMS = {rooms_src}\n\nEXITS = {exits_src}\n\nMOBILES = {mobiles_src}\n"


def export_area(area_slug: str, directory: str | None = None) -> tuple[str, dict, list]:
    """Write ``<area>.py`` to the areas directory; return ``(path, rooms, exits)``.

    ``directory`` defaults to the git source tree; tests pass a temp dir.
    """
    manifest = build_area_manifest(area_slug)
    rooms, exits = manifest["rooms"], manifest["exits"]
    directory = directory or _areas_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{area_slug}.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_render_manifest(manifest))
    return path, rooms, exits


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _find_room(area_slug: str, room_key: str):
    """Return an existing room with this area+key, or ``None``."""
    for room in search_tag(room_key, category=ROOM_KEY_CATEGORY):
        if room.tags.has(area_slug, category=AREA_TAG_CATEGORY):
            return room
    return None


def load_area_data(
    area_slug: str, rooms: dict, exits: list, mobiles: list | None = None
) -> dict:
    """Spawn/refresh ``rooms`` then wire ``exits``; return ``{room_key: room}``.

    Idempotent: rooms already tagged with this area+key are reused rather than
    duplicated, and an exit that already exists is left untouched — so loading
    the same area twice is safe.
    """
    validate_area_exit_doors(exits)
    policies: dict[str, dict] = {}
    environments: dict[str, dict] = {}
    sectors: dict[str, str] = {}
    for room_key, prototype in rooms.items():
        raw_policy = None
        raw_environment = None
        raw_sector = None
        for attr in prototype.get("attrs", []):
            if attr[0] == ROOM_POLICY_ATTRIBUTE:
                raw_policy = attr[1]
            elif attr[0] == ROOM_ENVIRONMENT_ATTRIBUTE:
                raw_environment = attr[1]
            elif attr[0] == SECTOR_ATTRIBUTE:
                raw_sector = attr[1]
        if raw_sector is not None and raw_sector not in SECTORS:
            raise ValueError("Invalid room sector in area data.")
        sectors[room_key] = raw_sector or "inside"
        try:
            validate_room_policy(raw_policy)
        except RoomPolicyError as err:
            raise ValueError(f"Invalid room policy in area data: {err}") from err
        policies[room_key] = {
            "version": ROOM_POLICY_VERSION,
            **asdict(validate_room_policy(raw_policy)),
        }
        try:
            environment = validate_room_environment(raw_environment)
        except RoomEnvironmentError as err:
            raise ValueError(f"Invalid room environment in area data: {err}") from err
        environments[room_key] = {
            "version": ROOM_ENVIRONMENT_VERSION,
            **asdict(environment),
            "light": environment.light.value,
        }
    key_to_room: dict[str, object] = {}
    for room_key, prototype in rooms.items():
        room = _find_room(area_slug, room_key)
        if room is None:
            (room,) = spawn(dict(prototype))
        room.tags.add(area_slug, category=AREA_TAG_CATEGORY)
        room.tags.add(room_key, category=ROOM_KEY_CATEGORY)
        room.attributes.add(ROOM_POLICY_ATTRIBUTE, policies[room_key])
        room.attributes.add(ROOM_ENVIRONMENT_ATTRIBUTE, environments[room_key])
        room.attributes.add(SECTOR_ATTRIBUTE, sectors[room_key])
        key_to_room[room_key] = room

    created_or_found_exits = []
    for from_key, direction, to_key, attrs in exits:
        src = key_to_room.get(from_key)
        dst = key_to_room.get(to_key)
        if src is None or dst is None:
            continue
        existing = next(
            (ex for ex in src.exits if ex.key == direction and ex.destination == dst),
            None,
        )
        if existing is None:
            existing = create_object(
                settings.BASE_EXIT_TYPECLASS,
                key=direction,
                aliases=attrs.get("aliases"),
                location=src,
                destination=dst,
            )
            created = True
        else:
            created = False
        created_or_found_exits.append((existing, attrs, created))

    # Configure doors only after the complete exit graph exists, so the second
    # side of a synchronized pair can resolve the first in the same load.
    for exit_obj, attrs, created in created_or_found_exits:
        if created:
            apply_door_area_data(exit_obj, attrs.get("door_state"))

    if mobiles:
        # MOB-05 validates all source references before any one placement can
        # create an NPC, then uses a stable load token to keep repeated loads
        # from duplicating successful copies. AREA-03 later supplies reset
        # tokens for recurring reconciliation.
        from systems.mob_spawning import (
            MobileSpawnError,
            reconcile_mobile_placement,
            validate_mobile_placements,
        )

        try:
            placements = validate_mobile_placements(area_slug, mobiles, key_to_room)
        except MobileSpawnError as err:
            logger.log_err(
                f"load_area: invalid mobile placements for {area_slug}: {err}"
            )
        else:
            for placement in placements:
                result = reconcile_mobile_placement(
                    f"area_load_{area_slug}", placement, key_to_room
                )
                if result.status == "failed":
                    logger.log_err(
                        f"load_area: mobile placement {placement.placement_key} "
                        f"failed for {area_slug}: {result.reason}"
                    )

    return key_to_room


def load_area_manifest_data(manifest: object) -> dict:
    """Load validated AREA-01B records, resolving external exits when available."""
    raw = validate_area_manifest(manifest)
    plan = compile_area_load_plan(manifests={raw["key"]: raw})
    data = _thaw_source(plan.registry.manifests[raw["key"]])
    if isinstance(data["exits"], list):
        return load_area_data(
            data["key"], data["rooms"], data["exits"], data["mobiles"]
        )

    legacy_rooms = {
        key: {
            "prototype_key": f"{data['key']}_{key}",
            "typeclass": settings.BASE_ROOM_TYPECLASS,
            "key": room["name"],
            "attrs": [
                ("desc", room["description"], None, ""),
                ("extra_descs", room["extra_descriptions"], None, ""),
                (ROOM_POLICY_ATTRIBUTE, room["policy"], None, ""),
                (ROOM_ENVIRONMENT_ATTRIBUTE, room["environment"], None, ""),
                (SECTOR_ATTRIBUTE, room["sector"], None, ""),
            ],
        }
        for key, room in data["rooms"].items()
    }
    local_exits = [
        (
            record["source_room"],
            record["name"],
            record["destination"]["room_key"],
            {"aliases": record["aliases"], "door_state": record["door"]},
        )
        for record in data["exits"].values()
        if record["destination"]["kind"] == "local"
    ]
    rooms = load_area_data(data["key"], legacy_rooms, local_exits, data["mobiles"])

    for room_key, record in data["rooms"].items():
        room = rooms[room_key]
        room.key = record["name"]
        room.attributes.add("desc", record["description"])
        room.attributes.add("extra_descs", record["extra_descriptions"])
        profiles = room.tags.get(category="weather_profile", return_list=True)
        for profile in profiles:
            room.tags.remove(profile, category="weather_profile")
        if record["weather_profile"]:
            room.tags.add(record["weather_profile"], category="weather_profile")

    for record_key, record in data["exits"].items():
        source = rooms[record["source_room"]]
        destination_data = record["destination"]
        destination = (
            rooms[destination_data["room_key"]]
            if destination_data["kind"] == "local"
            else _find_room(destination_data["area_key"], destination_data["room_key"])
        )
        if destination is None:
            continue
        exit_obj = next(
            (
                candidate
                for candidate in source.exits
                if candidate.tags.has(record_key, category=EXIT_KEY_CATEGORY)
            ),
            None,
        )
        if exit_obj is None:
            exit_obj = next(
                (
                    candidate
                    for candidate in source.exits
                    if candidate.key == record["name"]
                    and candidate.destination == destination
                ),
                None,
            )
        if exit_obj is None:
            exit_obj = create_object(
                settings.BASE_EXIT_TYPECLASS,
                key=record["name"],
                aliases=record["aliases"] or None,
                location=source,
                destination=destination,
            )
        exit_obj.tags.add(record_key, category=EXIT_KEY_CATEGORY)
        exit_obj.key = record["name"]
        exit_obj.aliases.clear()
        if record["aliases"]:
            exit_obj.aliases.add(record["aliases"])
        exit_obj.attributes.add("desc", record["description"])
        if destination_data["kind"] == "external":
            set_external_destination(
                exit_obj, destination_data["area_key"], destination_data["room_key"]
            )
        if door_area_data(exit_obj) != record["door"]:
            apply_door_area_data(exit_obj, record["door"])
    return rooms


def _literal_manifest_from_module(module_name: str) -> dict[str, Any] | None:
    """Read MANIFEST without executing a new-format area module."""
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        return None
    try:
        with open(spec.origin, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=spec.origin)
    except (OSError, SyntaxError) as err:
        raise AreaManifestError("Could not read the area manifest safely.") from err
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "MANIFEST"
    ]
    if not assignments:
        return None

    def is_docstring(node: ast.AST) -> bool:
        """Return whether an AST node is the module's harmless docstring."""
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

    if len(assignments) != 1 or any(
        not isinstance(node, ast.Assign) and not is_docstring(node)
        for node in tree.body
    ):
        raise AreaManifestError("Area manifest modules may contain only MANIFEST data.")
    try:
        return validate_area_manifest(ast.literal_eval(assignments[0].value))
    except (ValueError, TypeError) as err:
        raise AreaManifestError("Area manifest must be a Python literal.") from err


def _freeze_source(value: Any) -> Any:
    """Recursively freeze normalized source data exposed by a registry."""
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_source(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_source(item) for item in value)
    return value


def _thaw_source(value: Any) -> Any:
    """Detach a mutable loader view from an already validated immutable plan."""
    if isinstance(value, Mapping):
        return {key: _thaw_source(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_source(item) for item in value]
    return value


def _manifest_module_names() -> list[str]:
    """List tracked area modules without importing their authored module bodies."""
    spec = importlib.util.find_spec("world.areas")
    if spec is None or not spec.submodule_search_locations:
        return []
    return sorted(
        f"world.areas.{item.name}"
        for location in spec.submodule_search_locations
        for item in pkgutil.iter_modules([location])
        if not item.ispkg and not item.name.startswith("_")
    )


def _source_prototypes(
    catalogs: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> dict[str, dict[str, Any]]:
    """Validate explicit data-only catalogs and index their globally unique keys.

    AREA-05A owns the on-disk catalog format.  This intentionally accepts a
    supplied catalog index now so area planning has a single source-only
    resolver and never falls through to database prototypes.
    """
    if catalogs is None:
        return {}
    if not isinstance(catalogs, Mapping):
        raise AreaRegistryError("Prototype catalogs must be keyed mappings.")
    indexed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for catalog_key in sorted(catalogs, key=str):
        records = catalogs[catalog_key]
        if not isinstance(catalog_key, str) or not isinstance(records, Mapping):
            errors.append("prototype catalog has an invalid record mapping")
            continue
        for record_key in sorted(records, key=str):
            record = records[record_key]
            path = f"catalog '{catalog_key}' prototype '{record_key}'"
            if not isinstance(record, Mapping):
                errors.append(f"{path} is not a mapping")
                continue
            key = record.get("prototype_key", record_key)
            try:
                key = _slug(key, "prototype key")
            except AreaManifestError:
                errors.append(f"{path} has an invalid prototype key")
                continue
            if key != record_key:
                errors.append(f"{path} key does not match its prototype_key")
            if key in indexed:
                errors.append(f"duplicate prototype key '{key}'")
                continue
            try:
                normalized = _normalize_data(record)
            except AreaManifestError:
                errors.append(f"{path} contains non-literal data")
                continue
            indexed[key] = normalized
    if errors:
        raise AreaRegistryError("; ".join(sorted(errors)))
    return indexed


def build_area_registry(
    enabled_areas: Sequence[str] | None = None,
    *,
    manifests: Mapping[str, object] | None = None,
    prototype_catalogs: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> AreaRegistry:
    """Compile enabled source manifests into one deterministic read-only index.

    The optional ``manifests`` argument makes this side-effect-free operation
    straightforward to use in validation and tests.  If omitted, all literal
    modules in ``world.areas`` are read without executing their contents.
    """
    errors: list[str] = []
    raw_manifests: dict[str, object] = {}
    if manifests is not None:
        if not isinstance(manifests, Mapping):
            raise AreaRegistryError("Area manifests must be keyed mappings.")
        raw_manifests = dict(manifests)
    else:
        for module_name in _manifest_module_names():
            try:
                data = _literal_manifest_from_module(module_name)
            except AreaManifestError as err:
                errors.append(f"{module_name.rsplit('.', 1)[-1]}: {err}")
                continue
            if data is not None:
                if data["key"] in raw_manifests:
                    errors.append(f"duplicate area key '{data['key']}'")
                raw_manifests[data["key"]] = data

    if enabled_areas is None:
        selected = sorted(raw_manifests)
    else:
        selected = []
        for key in enabled_areas:
            try:
                selected.append(_slug(key, "enabled area key"))
            except AreaManifestError as err:
                errors.append(str(err))
        if len(set(selected)) != len(selected):
            errors.append("enabled area keys must be unique")
        selected = sorted(set(selected))

    normalized: dict[str, dict[str, Any]] = {}
    for source_key in sorted(raw_manifests, key=str):
        if not isinstance(source_key, str):
            errors.append("area manifest source key must be text")
            continue
        try:
            manifest = validate_area_manifest(
                raw_manifests[source_key], validate_runtime_prototypes=False
            )
        except AreaManifestError as err:
            errors.append(f"area '{source_key}': {err}")
            continue
        if source_key != manifest["key"]:
            errors.append(
                f"area '{source_key}' manifest key must match its source area key"
            )
        if manifest["key"] in normalized:
            errors.append(f"duplicate area key '{manifest['key']}'")
        normalized[manifest["key"]] = manifest

    # A named area means its declared dependency closure, not an accidental
    # single-file load.  This leaves undeclared external links invalid.
    pending = list(selected)
    while pending:
        key = pending.pop()
        manifest = normalized.get(key)
        if manifest is None:
            continue
        for dependency in manifest["dependencies"]:
            if dependency not in selected:
                selected.append(dependency)
                pending.append(dependency)
    selected.sort()

    for key in selected:
        if key not in normalized:
            errors.append(f"enabled area '{key}' is missing or invalid")

    rooms: dict[str, dict[str, Any]] = {}
    for area_key in selected:
        manifest = normalized.get(area_key)
        if manifest is None:
            continue
        for dependency in manifest["dependencies"]:
            if dependency not in selected:
                errors.append(
                    f"area '{area_key}' has undeclared enabled dependency '{dependency}'"
                )
        for room_key, room in manifest["rooms"].items():
            reference = canonical_room_reference(area_key, room_key)
            if reference in rooms:
                errors.append(f"duplicate room reference '{reference}'")
            rooms[reference] = room

    for area_key in selected:
        manifest = normalized.get(area_key)
        if manifest is None or not isinstance(manifest["exits"], dict):
            continue
        for exit_key, record in manifest["exits"].items():
            destination = record["destination"]
            if destination["kind"] != "external":
                continue
            target_area, target_room = destination["area_key"], destination["room_key"]
            target = canonical_room_reference(target_area, target_room)
            path = f"area '{area_key}' exit '{exit_key}'"
            if target_area not in selected:
                errors.append(
                    f"{path} targets missing or disabled area '{target_area}'"
                )
            elif target not in rooms:
                errors.append(f"{path} targets missing room '{target}'")
            if target_area not in manifest["dependencies"]:
                errors.append(f"{path} has undeclared dependency '{target_area}'")

    try:
        prototypes = _source_prototypes(prototype_catalogs)
    except AreaRegistryError as err:
        errors.append(str(err))
        prototypes = {}
    if errors:
        raise AreaRegistryError("; ".join(sorted(errors)))
    return AreaRegistry(
        manifests=MappingProxyType(
            {key: _freeze_source(normalized[key]) for key in selected}
        ),
        rooms=MappingProxyType(
            {key: _freeze_source(rooms[key]) for key in sorted(rooms)}
        ),
        prototypes=MappingProxyType(
            {key: _freeze_source(prototypes[key]) for key in sorted(prototypes)}
        ),
    )


def resolve_room_reference(
    registry: AreaRegistry, reference: object, current_area: object
) -> Mapping[str, Any]:
    """Resolve an exact canonical room reference from a source registry only."""
    canonical = normalize_room_reference(reference, current_area)
    try:
        return registry.rooms[canonical]
    except KeyError as err:
        raise AreaRegistryError(
            f"Unknown managed room reference '{canonical}'."
        ) from err


def resolve_prototype(
    registry: AreaRegistry, prototype_key: object
) -> Mapping[str, Any]:
    """Resolve an exact source prototype key without a database/search fallback."""
    key = _slug(prototype_key, "prototype key")
    try:
        return registry.prototypes[key]
    except KeyError as err:
        raise AreaRegistryError(f"Unknown source prototype '{key}'.") from err


def compile_area_load_plan(
    enabled_areas: Sequence[str] | None = None,
    *,
    manifests: Mapping[str, object] | None = None,
    prototype_catalogs: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> AreaLoadPlan:
    """Validate one source closure and return its complete immutable load plan.

    This function deliberately creates no objects, reads no database prototypes,
    and returns no mutable source records.  Apply/reset code must consume this
    plan rather than re-reading area module globals between validation stages.
    """
    try:
        registry = build_area_registry(
            enabled_areas, manifests=manifests, prototype_catalogs=prototype_catalogs
        )
    except AreaRegistryError as err:
        raise AreaPlanError(str(err)) from err

    errors: list[str] = []
    exits: list[tuple[str, str, Mapping[str, Any]]] = []
    services: list[tuple[str, str, Mapping[str, Any]]] = []
    mobiles: list[tuple[str, Mapping[str, Any]]] = []
    objects: list[tuple[str, str, Mapping[str, Any]]] = []
    service_fields = (
        "mobile_policy",
        "mobile_specials",
        "mobile_behavior_profile",
        "trainer_profile",
        "shop_profile",
    )
    for area_key, manifest in registry.manifests.items():
        if not isinstance(manifest["exits"], tuple | Mapping):
            errors.append(f"area '{area_key}' exits have an invalid shape")
        elif isinstance(manifest["exits"], Mapping):
            for exit_key, record in manifest["exits"].items():
                exits.append((area_key, exit_key, record))
        for placement in manifest["mobiles"]:
            mobiles.append((area_key, placement))
            _plan_prototype_reference(
                registry,
                placement["prototype_key"],
                "typeclasses.characters.Character",
                f"area '{area_key}' mobile '{placement['placement_key']}'",
                errors,
            )
        for placement_key, placement in manifest["objects"].items():
            objects.append((area_key, placement_key, placement))
            _plan_object_prototype_references(
                registry,
                placement,
                f"area '{area_key}' object '{placement_key}'",
                errors,
            )

    for prototype_key, prototype in registry.prototypes.items():
        for field in service_fields:
            if field in prototype:
                services.append((prototype_key, field, prototype))
    if errors:
        raise AreaPlanError("; ".join(sorted(errors)))
    return AreaLoadPlan(
        registry=registry,
        source_prototypes=tuple(sorted(registry.prototypes.items())),
        rooms=tuple(sorted(registry.rooms.items())),
        exits_and_doors=tuple(sorted(exits)),
        service_assignments=tuple(sorted(services)),
        mobile_placements=tuple(sorted(mobiles)),
        object_placements=tuple(sorted(objects)),
    )


def _plan_prototype_reference(
    registry: AreaRegistry,
    prototype_key: object,
    expected_typeclass: str,
    path: str,
    errors: list[str],
) -> None:
    """Record an exact source-catalog reference error without fail-fast search."""
    try:
        prototype = resolve_prototype(registry, prototype_key)
    except AreaRegistryError:
        errors.append(f"{path} references unknown source prototype '{prototype_key}'")
        return
    if prototype.get("typeclass") != expected_typeclass:
        errors.append(f"{path} references a wrong-kind source prototype '{prototype_key}'")


def _plan_object_prototype_references(
    registry: AreaRegistry,
    node: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Check every already-schema-validated object tree node against the catalog."""
    _plan_prototype_reference(
        registry,
        node["prototype_key"],
        "typeclasses.objects.Item",
        path,
        errors,
    )
    for index, child in enumerate(node["contents"]):
        _plan_object_prototype_references(
            registry, child, f"{path} contents[{index}]", errors
        )


def area_plan_summary(plan: AreaLoadPlan) -> str:
    """Render a bounded Builder-safe success summary with no filesystem details."""
    return (
        "Area plan valid: "
        f"{len(plan.registry.manifests)} area(s), {len(plan.source_prototypes)} prototype(s), "
        f"{len(plan.rooms)} room(s), {len(plan.exits_and_doors)} exit(s), "
        f"{len(plan.mobile_placements)} mobile placement(s), and "
        f"{len(plan.object_placements)} object placement(s)."
    )


def load_area(area_slug: str) -> dict:
    """Load a safe AREA-01A manifest, falling back to a legacy module reader."""
    from importlib import import_module

    try:
        area_slug = _slug(area_slug, "key")
        manifest = _literal_manifest_from_module(f"world.areas.{area_slug}")
        if manifest is not None:
            if manifest["key"] != area_slug:
                raise AreaManifestError(
                    "Manifest key does not match its requested area."
                )
            return load_area_manifest_data(manifest)
    except AreaManifestError as err:
        logger.log_err(f"load_area: invalid manifest for {area_slug}: {err}")
        return {}
    try:
        module = import_module(f"world.areas.{area_slug}")
    except ModuleNotFoundError:
        logger.log_err(f"load_area: no area module 'world.areas.{area_slug}'")
        return {}
    rooms = getattr(module, "ROOMS", {})
    exits = getattr(module, "EXITS", [])
    mobiles = getattr(module, "MOBILES", [])
    return load_area_data(area_slug, rooms, exits, mobiles)

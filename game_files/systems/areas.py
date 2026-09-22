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
from dataclasses import asdict
from pprint import pformat
from typing import Any

from django.conf import settings
from evennia import create_object
from evennia.prototypes.spawner import prototype_from_object, spawn
from evennia.utils import logger
from evennia.utils.search import search_tag
from systems.doors import apply_door_area_data, door_area_data, validate_area_exit_doors
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
from world.build_schema import as_slug

AREA_TAG_CATEGORY = "area"
ROOM_KEY_CATEGORY = "room_key"

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
    }
)
MAX_MANIFEST_TEXT_LENGTH = 2_000
MAX_MANIFEST_COLLECTION_SIZE = 2_000
MAX_MANIFEST_DEPTH = 20
MAX_LIFESPAN_PULSES = 1_000_000


class AreaManifestError(ValueError):
    """A tracked area manifest is malformed, unsafe, or unsupported."""


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
    if isinstance(value, (list, tuple)):
        return [_normalize_data(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise AreaManifestError("Manifest mapping keys must be strings.")
        return {key: _normalize_data(item) for key, item in value.items()}
    raise AreaManifestError("Manifest data may contain only primitive values.")


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


def validate_area_manifest(manifest: object) -> dict[str, Any]:
    """Validate and normalize a version-one, data-only area manifest."""
    try:
        data = _normalize_data(manifest)
    except AreaManifestError:
        raise
    if not isinstance(data, dict):
        raise AreaManifestError("Manifest must be a mapping.")
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
    if not isinstance(data["rooms"], dict) or not isinstance(data["exits"], list):
        raise AreaManifestError("Manifest rooms and exits have invalid section types.")
    if not isinstance(data["mobiles"], list):
        raise AreaManifestError("Manifest mobiles has an invalid section type.")
    return data


def manifest_fingerprint(manifest: object) -> str:
    """Return the SHA-256 fingerprint of validated, canonical manifest data."""
    data = validate_area_manifest(manifest)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_area_manifest(area_slug: str) -> dict[str, Any]:
    """Snapshot a live area in AREA-01A's versioned envelope."""
    area_slug = _slug(area_slug, "key")
    rooms, exits = build_area_data(area_slug)
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
            return load_area_data(
                area_slug,
                manifest["rooms"],
                manifest["exits"],
                manifest["mobiles"],
            )
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

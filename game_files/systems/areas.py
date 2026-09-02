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

import os
from pprint import pformat

from django.conf import settings
from evennia import create_object
from evennia.prototypes.spawner import prototype_from_object, spawn
from evennia.utils import logger
from evennia.utils.search import search_tag
from world.build_schema import as_slug

AREA_TAG_CATEGORY = "area"
ROOM_KEY_CATEGORY = "room_key"

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
    for old in room.tags.get(category=AREA_TAG_CATEGORY, return_list=True):
        room.tags.remove(old, category=AREA_TAG_CATEGORY)
    room.tags.add(area_slug, category=AREA_TAG_CATEGORY)
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


def _render_module(area_slug: str, rooms: dict, exits: list) -> str:
    """Render a readable, valid Python area module."""
    header = (
        f'"""Area "{area_slug}" — generated by the build command.\n\n'
        "Edit in-game and re-export, or hand-edit and reload; both round-trip\n"
        'through systems.areas.load_area("%s").\n"""\n\n' % area_slug
    )
    rooms_src = pformat(rooms, width=88, sort_dicts=True)
    exits_src = pformat(exits, width=88)
    return f"{header}ROOMS = {rooms_src}\n\nEXITS = {exits_src}\n"


def export_area(area_slug: str, directory: str | None = None) -> tuple[str, dict, list]:
    """Write ``<area>.py`` to the areas directory; return ``(path, rooms, exits)``.

    ``directory`` defaults to the git source tree; tests pass a temp dir.
    """
    rooms, exits = build_area_data(area_slug)
    directory = directory or _areas_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{area_slug}.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_render_module(area_slug, rooms, exits))
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


def load_area_data(area_slug: str, rooms: dict, exits: list) -> dict:
    """Spawn/refresh ``rooms`` then wire ``exits``; return ``{room_key: room}``.

    Idempotent: rooms already tagged with this area+key are reused rather than
    duplicated, and an exit that already exists is left untouched — so loading
    the same area twice is safe.
    """
    key_to_room: dict[str, object] = {}
    for room_key, prototype in rooms.items():
        room = _find_room(area_slug, room_key)
        if room is None:
            (room,) = spawn(dict(prototype))
        room.tags.add(area_slug, category=AREA_TAG_CATEGORY)
        room.tags.add(room_key, category=ROOM_KEY_CATEGORY)
        key_to_room[room_key] = room

    for from_key, direction, to_key, attrs in exits:
        src = key_to_room.get(from_key)
        dst = key_to_room.get(to_key)
        if src is None or dst is None:
            continue
        if any(ex.key == direction and ex.destination == dst for ex in src.exits):
            continue
        create_object(
            settings.BASE_EXIT_TYPECLASS,
            key=direction,
            aliases=attrs.get("aliases"),
            location=src,
            destination=dst,
        )

    return key_to_room


def load_area(area_slug: str) -> dict:
    """Import ``world.areas.<area>`` and load it into the live world."""
    from importlib import import_module

    try:
        module = import_module(f"world.areas.{area_slug}")
    except ModuleNotFoundError:
        logger.log_err(f"load_area: no area module 'world.areas.{area_slug}'")
        return {}
    rooms = getattr(module, "ROOMS", {})
    exits = getattr(module, "EXITS", [])
    return load_area_data(area_slug, rooms, exits)

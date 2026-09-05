"""
The |wbuild|n command — a unified, context-aware builder front-end.

Instead of memorising a dozen separate verbs (``dig``/``create``/``desc``/``set``
/``spawn`` …), a builder runs |wbuild|n / |wedit|n to enter a *sticky editing
context* bound to a single object, then uses flat, self-listing verbs that act on
that object.  The context is a temporary, non-persistent cmdset added to the
caller — the same mechanism Evennia's line editor uses — so the verbs only exist
while you're editing and ``done`` cleanly removes them.

The classic Evennia building commands are deliberately left in place as "expert
mode"; |wbuild|n is sugar over the same primitives (typeclasses, attributes,
tags, prototypes), not a replacement.

Rooms, item templates, and NPC templates all share this editing context; the
field schema decides what each target exposes.
"""

from commands.command import Command
from django.conf import settings
from evennia import CmdSet, create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import (PROTOTYPE_TAG_CATEGORY,
                                           delete_prototype, save_prototype,
                                           search_prototype)
from evennia.prototypes.spawner import spawn
from evennia.utils import logger
from evennia.utils.eveditor import EvEditor
from evennia.utils.search import search_tag
from evennia.utils.utils import inherits_from
from systems.action_policy import ActionCategory
from systems.areas import (area_index, area_of, assign_area, export_area,
                           load_area, room_key_of, rooms_in_area)
from world.build_schema import (ITEM_TYPES, TYPE_FIELDS, as_slug, schema_for,
                                schema_for_prototype)

# Standard directions -> (reverse direction, short aliases).  Used to keep dug
# exits two-way and to alias n/s/e/w/u/d like Evennia's own tunnel command.
_DIRECTIONS: dict[str, tuple[str, list[str]]] = {
    "north": ("south", ["n"]),
    "south": ("north", ["s"]),
    "east": ("west", ["e"]),
    "west": ("east", ["w"]),
    "northeast": ("southwest", ["ne"]),
    "northwest": ("southeast", ["nw"]),
    "southeast": ("northwest", ["se"]),
    "southwest": ("northeast", ["sw"]),
    "up": ("down", ["u"]),
    "down": ("up", ["d"]),
    "in": ("out", []),
    "out": ("in", []),
}

_BUILDER_LOCK = "cmd:perm(Builder)"

# Input prompt shown while a builder is in an editing session. The game has no
# prompt otherwise, so its mere presence makes it obvious you're still bound to
# a (possibly remote) room — and it's cleared again the moment you leave.
_BUILD_PROMPT = "editing> "

# Typeclass of buildable items, and the type keywords accepted by `edit new`.
# Items are a single typeclass; specialisation is the item's `type` attribute
# (see world/build_schema.py), set in the editor with `set type <kind>`.
_ITEM_TYPECLASS = "typeclasses.objects.Item"
_NPC_TYPECLASS = "typeclasses.characters.Character"
_NEW_TYPES = ("room", "item", "npc")


def _is_prototype(target) -> bool:
    """A build target is either a live object or a prototype dict (a template)."""
    return isinstance(target, dict)


def _is_room(obj) -> bool:
    return not _is_prototype(obj) and inherits_from(
        obj, "evennia.objects.objects.DefaultRoom"
    )


def _schema(target):
    """The editable-field schema for a build target (live object or prototype)."""
    if _is_prototype(target):
        return schema_for_prototype(target)
    return schema_for(target)


def _flatten_prototype(proto: dict) -> dict:
    """Convert a stored prototype into the editor's flat form.

    Evennia normalises arbitrary fields into an ``attrs`` list on save (reserved
    keys like ``key``/``typeclass`` stay top-level).  The build editor works with
    fields as plain top-level keys, so flatten the ``attrs`` back out on load.
    """
    flat = {k: v for k, v in proto.items() if k != "attrs"}
    for attr in proto.get("attrs", []):
        flat[attr[0]] = attr[1]  # (name, value[, category, locks])
    return flat


def _find_prototype(key: str, typeclass: str | None = None):
    """Return a prototype of ``typeclass`` with this exact key, or ``None``.

    ``search_prototype`` matches partially, so filter for an exact key and our
    expected typeclass when supplied.
    """
    for proto in search_prototype(key):
        if proto.get("prototype_key") == key and (
            typeclass is None or proto.get("typeclass") == typeclass
        ):
            return _flatten_prototype(proto)
    return None


def _find_item_prototype(key: str):
    """Return the item prototype (flat form) with this exact key, or ``None``."""
    return _find_prototype(key, _ITEM_TYPECLASS)


def _find_npc_prototype(key: str):
    """Return the NPC prototype (flat form) with this exact key, or ``None``."""
    return _find_prototype(key, _NPC_TYPECLASS)


# ---------------------------------------------------------------------------
# Edit-context lifecycle + shared rendering
# ---------------------------------------------------------------------------


def _enter_build_mode(caller, target) -> None:
    """Bind ``target``, add the sticky build cmdset, and arm the edit prompt.

    The prompt is re-emitted after every command by
    ``commands.command._PromptPersistMixin`` (it reads ``ndb._prompt``), so it
    stays visible no matter what the builder types — we only arm it here.
    """
    caller.ndb._build_target = target
    caller.ndb._build_del_pending = None
    caller.ndb._prompt = _BUILD_PROMPT
    caller.cmdset.add(BuildModeCmdSet, persistent=False)


def _exit_build_mode(caller) -> None:
    """Remove the build cmdset, clear the context, and drop the edit prompt."""
    caller.cmdset.remove(BuildModeCmdSet)
    caller.ndb._build_target = None
    caller.ndb._build_del_pending = None
    caller.ndb._prompt = None
    caller.msg(prompt="")


def _header(target) -> str:
    # Show what's being edited and what kind it is. Prototypes (templates) are
    # marked as such so a builder never confuses a template with a live copy.
    if _is_prototype(target):
        kind = (
            "NPC"
            if target.get("typeclass") == _NPC_TYPECLASS
            else (target.get("type") or "item").capitalize()
        )
        return f"|w[build: {target.get('key')} ({kind} prototype)]|n"
    label = type(target).__name__
    item_type = target.db.type
    if item_type:
        label = item_type.capitalize()
    return f"|w[build: {target.key} (#{target.id}, {label})]|n"


def _crop(text: str) -> str:
    text = str(text)
    return text if len(text) <= 60 else text[:57] + "..."


def _field_value(target, name: str, field) -> str:
    """Render the current value of one field for ``show``."""
    if _is_prototype(target):
        if field.kind == "type":
            return target.get("type") or "|x(generic item)|n"
        value = target.get("key" if field.kind == "key" else (field.target or name))
        return _crop(value) if value not in (None, "") else "|x(unset)|n"
    if field.kind == "key":
        return target.key
    if field.kind == "type":
        return target.db.type or "|x(generic item)|n"
    if field.kind == "attr":
        value = target.attributes.get(field.target or name)
        return _crop(value) if value is not None else "|x(unset)|n"
    if field.kind == "tag":
        tags = target.tags.get(category=field.target or name, return_list=True)
        return ", ".join(tags) if tags else "|x(unset)|n"
    return "?"


def _render_show(target) -> str:
    """A read-out of every field (and, for rooms, exits) of ``target``."""
    schema = _schema(target)
    lines = []
    if schema:
        for name, field in schema.items():
            lines.append(f"  |y{name:<8}|n {_field_value(target, name, field)}")
    if _is_room(target):
        exits = [ex for ex in target.exits if ex.destination]
        if exits:
            joined = ", ".join(f"{ex.key} -> {ex.destination.key}" for ex in exits)
            lines.append(f"  |yexits|n    {joined}")
        else:
            lines.append("  |yexits|n    |x(none)|n")
    return "\n".join(lines) if lines else "  |x(nothing editable yet)|n"


def _render_fields(target) -> str:
    """List the editable fields of ``target`` with their blurbs."""
    schema = _schema(target)
    if not schema:
        return "There are no editable fields for this object yet."
    lines = ["|wEditable fields|n (set with |wset <field> <value>|n):"]
    for name, field in schema.items():
        lines.append(f"  |y{name:<8}|n {field.blurb}")
    return "\n".join(lines)


def _type_attr_names(item_type) -> list[str]:
    """Attribute/key names owned by an item type's extra fields (for clearing)."""
    return [
        fld.target or fname for fname, fld in TYPE_FIELDS.get(item_type, {}).items()
    ]


def _set_item_type(item, value: str) -> None:
    """Set a live item's ``type``, dropping the previous type's attributes.

    Both weapon and armor expose a ``subtype`` field with different allowed
    values, so a leftover value from the old type would mislead — clear the old
    type's fields entirely on every change.  ``"none"`` reverts to a generic item.
    """
    new_type = None if value == "none" else value
    if item.db.type == new_type:
        return
    for attr in _type_attr_names(item.db.type):
        item.attributes.remove(attr)
    item.db.type = new_type


def _set_prototype_type(proto: dict, value: str) -> None:
    """As :func:`_set_item_type`, but for a prototype dict instead of an object."""
    new_type = None if value == "none" else value
    if proto.get("type") == new_type:
        return
    for attr in _type_attr_names(proto.get("type")):
        proto.pop(attr, None)
    if new_type:
        proto["type"] = new_type
    else:
        proto.pop("type", None)


def _apply_field(target, name: str, field, value) -> None:
    """Write a validated field ``value`` to ``target`` per its schema kind."""
    if _is_prototype(target):
        if field.kind == "key":
            target["key"] = value
        elif field.kind == "type":
            _set_prototype_type(target, value)
        else:  # attr
            target[field.target or name] = value
        save_prototype(target)  # templates persist on every change
        return
    if field.kind == "key":
        target.key = value
    elif field.kind == "type":
        _set_item_type(target, value)
    elif field.kind == "attr":
        target.attributes.add(field.target or name, value)
    elif field.kind == "tag":
        if name == "area":
            assign_area(target, value)
        else:
            category = field.target or name
            for old in target.tags.get(category=category, return_list=True):
                target.tags.remove(old, category=category)
            target.tags.add(value, category=category)


# ---------------------------------------------------------------------------
# Entry command (always available, lock-gated to Builders)
# ---------------------------------------------------------------------------


class CmdBuild(Command):
    """
    Build and edit the world.

    Usage:
      build                   show what you can build and what you're editing
      edit here               edit the room you're standing in
      edit new room <name>    create a fresh unlinked room and go into it
      edit new item <name>    create a new item *template* and edit it
      edit new npc <name>     create an NPC template and spawn a copy here
      edit item <name>        edit an existing item template
      edit npc <name>         edit an existing NPC template
      edit <object>           edit a live room, item, or NPC by name/#dbref

    Items are authored as |ytemplates|n (prototypes), then stamped into the world
    as copies — like DIKU object vnums.  |wedit new item|n makes a template;
    |w@spawn <key>|n places a copy of it in your room; |witems|n lists all
    templates. Give a template a kind with |wset type <kind>|n; |wfields|n shows
    whether that kind currently has type-specific fields. Editing a placed copy
    (|wedit <object>|n) is a one-off and never changes the template.

    NPCs are Character objects without an Account puppeting them. Like items,
    they are authored as templates; |wedit new npc|n also spawns one copy in
    your current room. Use |wnpcs|n to browse templates and |w@spawn <key>|n
    for more copies.

    |wedit new room|n starts an area "offline": a standalone room with no exits,
    so you can build and review it before |wlink|ning it into the live world.

    |wbuild|n opens a sticky editing context bound to one object.  While
    editing, flat verbs act on that object:

      fields                list the editable fields for what you're editing
      show                  show the object's current fields and exits
      set <field> <value>   set a field (e.g. |wset desc A dusty hall.|n)
      desc                  open the multi-line editor for the description
      dig <dir> = <name>    dig a new room with two-way exits
      link <dir> = <room>   add an exit to an existing room
      unlink <dir>          remove an exit
      area <name>           assign this room to an area (for export)
      export                save this room's area to a git-tracked file
      del                   delete what you're editing (type del twice)
      done                  leave the editing context

    The classic builder commands (dig, create, set, desc, spawn, ...) still
    work outside build mode for expert use.
    """

    key = "build"
    aliases = ["edit"]
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        caller = self.caller
        arg = self.args.strip()
        lowered = arg.lower()

        if not arg or lowered == "help":
            self._status()
            return

        if lowered == "new" or lowered.startswith("new "):
            # Everything after "new" is an optional type keyword + name.
            self._create_new(arg[len("new") :].strip())
            return

        if lowered == "item" or lowered.startswith("item "):
            # 'edit item <name>' edits an existing item *prototype* (template).
            self._edit_item_prototype(arg[len("item") :].strip())
            return

        if lowered == "npc" or lowered.startswith("npc "):
            # 'edit npc <name>' edits an existing NPC *prototype* (template).
            self._edit_npc_prototype(arg[len("npc") :].strip())
            return

        if lowered == "here":
            target = caller.location
            if target is None:
                caller.msg("You are not in any room to edit.")
                return
        else:
            target = caller.search(arg, global_search=True)
            if not target:
                return  # search() already messaged the failure
            # 'edit north' finds the *exit*; redirect to the room it leads to,
            # since exits aren't editable but the room beyond is what you mean.
            if getattr(target, "destination", None):
                caller.msg(
                    f"(|w{target.key}|n leads to |y{target.destination.key}|n — "
                    "editing that room.)"
                )
                target = target.destination

        _enter_build_mode(caller, target)
        caller.msg(_header(target) + "\n" + _render_show(target))

    def _create_new(self, rest: str) -> None:
        """Dispatch ``edit new <room|item|npc> [<name>]`` to its creator.

        A type keyword is required so each flow is explicit: ``room`` teleports
        you into a fresh standalone room, ``item`` creates a template, and
        ``npc`` creates a template plus one live copy in the current room.
        """
        parts = rest.split(None, 1)
        new_type = parts[0].lower() if parts else ""
        name = parts[1].strip() if len(parts) > 1 else ""
        if new_type not in _NEW_TYPES:
            self.caller.msg(f"Usage: edit new <{'|'.join(_NEW_TYPES)}> [name]")
            return

        if new_type == "room":
            self._create_room(name)
        elif new_type == "item":
            self._create_item(name)
        else:
            self._create_npc(name)

    def _create_room(self, name: str) -> None:
        """Create a standalone, unlinked room and move the builder into it.

        This is how an area is started "offline": the new room has no exits to
        (or from) the live world, so a builder can lay out and peer-review a
        half-finished area before any |wlink|n wires it into the game.
        """
        caller = self.caller
        name = name or "An Unnamed Room"
        new_room = create_object(settings.BASE_ROOM_TYPECLASS, key=name)
        origin = caller.location
        caller.move_to(new_room, quiet=True, move_type="teleport")
        _enter_build_mode(caller, new_room)

        note = (
            f"Created |y{name}|n (#{new_room.id}) and moved you inside. It is not "
            "linked to anything yet — set its |wname|n/|wdesc|n, give it an "
            "|warea <name>|n so it can be listed and exported, then |wdig|n to "
            "grow the area or |wlink|n it into the live world when it's ready."
        )
        if origin:
            note += (
                f"\nYou came from |y{origin.key}|n (#{origin.id}); type "
                f"|wtel #{origin.id}|n to return."
            )
        caller.msg(note)
        caller.msg(_header(new_room) + "\n" + _render_show(new_room))

    def _create_item(self, name: str) -> None:
        """Create a new item *prototype* (template) and bind it for editing.

        An item is authored as a template, not a one-off: this makes a prototype
        you edit here and later stamp copies of into the world with
        ``@spawn <key>``.  The prototype persists as soon as it's created, so it
        shows up in |witems|n immediately.
        """
        caller = self.caller
        if not name:
            caller.msg("Usage: edit new item <name>")
            return
        key = as_slug(name)
        existing = _find_prototype(key)
        if existing:
            if existing.get("typeclass") == _ITEM_TYPECLASS:
                caller.msg(
                    f"An item prototype '|y{key}|n' already exists — edit it with "
                    f"|wedit item {key}|n."
                )
            else:
                caller.msg(f"Prototype key '|y{key}|n' is already in use.")
            return
        proto = {
            "prototype_key": key,
            "key": name,
            "typeclass": _ITEM_TYPECLASS,
            "weight": 0.0,
            "value": 0,
            "wear_locations": [],
        }
        save_prototype(proto)
        _enter_build_mode(caller, proto)
        caller.msg(
            f"Created item prototype |y{key}|n. Set its kind with "
            f"|wset type <kind>|n; place copies with |w@spawn {key}|n."
        )
        caller.msg(_header(proto) + "\n" + _render_show(proto))

    def _edit_item_prototype(self, name: str) -> None:
        """Bind an existing item prototype (template) for editing."""
        caller = self.caller
        if not name:
            caller.msg("Usage: edit item <name>")
            return
        key = as_slug(name)
        proto = _find_item_prototype(key)
        if proto is None:
            caller.msg(
                f"No item prototype '|y{key}|n'. See |witems|n, or create it with "
                f"|wedit new item {name}|n."
            )
            return
        _enter_build_mode(caller, dict(proto))  # edit a mutable copy
        caller.msg(
            _header(caller.ndb._build_target)
            + "\n"
            + _render_show(caller.ndb._build_target)
        )

    def _create_npc(self, name: str) -> None:
        """Create an NPC template and spawn one copy in the current room."""
        caller = self.caller
        if not name:
            caller.msg("Usage: edit new npc <name>")
            return
        if not _is_room(caller.location):
            caller.msg("You must be in a room to create and spawn an NPC.")
            return

        key = as_slug(name)
        existing = _find_prototype(key)
        if existing:
            if existing.get("typeclass") == _NPC_TYPECLASS:
                caller.msg(
                    f"An NPC prototype '|y{key}|n' already exists — edit it with "
                    f"|wedit npc {key}|n."
                )
            else:
                caller.msg(f"Prototype key '|y{key}|n' is already in use.")
            return

        # These are the canonical inputs written to a finished PC by chargen.
        # Effective combat values are derived by Character.stats; builders can
        # add explicit overrides through the regular `set` verb when needed.
        proto = {
            "prototype_key": key,
            "key": name,
            "typeclass": _NPC_TYPECLASS,
            "is_player_character": False,
            "gender": "unspecified",
            "age": 18,
            "char_class": "Fighter",
            "background": "",
            "species": "Human",
            "size": "Medium",
            "alignment": "Neutral",
            "languages": ["Common"],
            "active_language": "Common",
            "skill_proficiencies": [],
            "strength": 8,
            "dexterity": 8,
            "constitution": 8,
            "intelligence": 8,
            "wisdom": 8,
            "charisma": 8,
            "level": 1,
            "xp": 0,
            "hp_base": 10,
            "hp_current": 9,
            "hit_die": 10,
            "speed": 30,
        }
        save_prototype(proto)
        (npc,) = spawn({**proto, "location": caller.location}, caller=caller)
        _enter_build_mode(caller, proto)
        caller.msg(
            f"Created NPC prototype |y{key}|n and spawned |y{npc.key}|n "
            f"(#{npc.id}) in |y{caller.location.key}|n. Place more copies with "
            f"|w@spawn {key}|n."
        )
        caller.msg(_header(proto) + "\n" + _render_show(proto))

    def _edit_npc_prototype(self, name: str) -> None:
        """Bind an existing NPC prototype (template) for editing."""
        caller = self.caller
        if not name:
            caller.msg("Usage: edit npc <name>")
            return
        key = as_slug(name)
        proto = _find_npc_prototype(key)
        if proto is None:
            caller.msg(
                f"No NPC prototype '|y{key}|n'. See |wnpcs|n, or create it with "
                f"|wedit new npc {name}|n."
            )
            return
        _enter_build_mode(caller, dict(proto))
        caller.msg(
            _header(caller.ndb._build_target)
            + "\n"
            + _render_show(caller.ndb._build_target)
        )

    def _status(self) -> None:
        caller = self.caller
        target = caller.ndb._build_target
        if target:
            caller.msg(_header(target) + "\n" + _render_show(target))
        else:
            caller.msg(
                "You are not editing anything.\n"
                "  |wedit here|n            edit the current room\n"
                "  |wedit new room <name>|n create a fresh unlinked room and go to it\n"
                "  |wedit new item <name>|n create a new item template and edit it\n"
                "  |wedit item <name>|n     edit an existing item template\n"
                "  |wedit new npc <name>|n  create a template and spawn an NPC here\n"
                "  |wedit npc <name>|n      edit an existing NPC template\n"
                "  |wedit <object>|n        edit a live room, item, or NPC by name/#dbref\n"
                "Type |whelp build|n for the full verb list."
            )


class CmdLoadArea(Command):
    """
    Load an exported area into the live world.

    Usage:
      loadarea <area>

    Reads game_files/world/areas/<area>.py (after it has been synced into the
    running game) and spawns its rooms and exits.  Idempotent: rooms and exits
    that already exist are reused, not duplicated, so it is safe to re-run after
    editing the file.
    """

    key = "loadarea"
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        area = self.args.strip().lower()
        if not area:
            self.caller.msg("Usage: loadarea <area>")
            return
        try:
            slug = as_slug(area)
        except ValueError as err:
            self.caller.msg(f"Invalid area name: {err}")
            return

        rooms = load_area(slug)
        if not rooms:
            self.caller.msg(
                f"No rooms loaded for area |y{slug}|n. Is "
                f"game_files/world/areas/{slug}.py present and synced?"
            )
            return
        self.caller.msg(f"Loaded |y{len(rooms)}|n room(s) for area |y{slug}|n.")


def _render_area_index() -> str:
    """A list of every area with its room count, or a hint if there are none."""
    index = area_index()
    if not index:
        return "No areas yet. Assign rooms with |warea <name>|n while editing one."
    lines = ["|wAreas|n:"]
    for area in sorted(index):
        lines.append(f"  |y{area:<20}|n {len(index[area])} room(s)")
    lines.append("Use |wrooms <area>|n to list the rooms in one.")
    return "\n".join(lines)


class CmdAreas(Command):
    """
    List all areas and how many rooms each contains.

    Usage:
      areas
    """

    key = "areas"
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        self.caller.msg(_render_area_index())


class CmdRooms(Command):
    """
    List the rooms in an area.

    Usage:
      rooms              list the rooms in your current room's area
      rooms <area>       list the rooms in a named area

    Each room is shown by its area key, display name, and #dbref so you can
    jump straight to it with |wedit <name>|n or |wedit #<dbref>|n.  Use
    |wareas|n for an overview of every area.
    """

    key = "rooms"
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        caller = self.caller
        arg = self.args.strip()

        if arg:
            try:
                slug = as_slug(arg)
            except ValueError as err:
                caller.msg(f"Invalid area name: {err}")
                return
        else:
            # Default to the area of the room the builder is standing in.
            here = caller.location
            if here is None:
                caller.msg("You are not in a room.")
                return
            slug = area_of(here)
            if not slug:
                caller.msg("This room has no area assigned yet.")
                return

        rooms = rooms_in_area(slug)
        if not rooms:
            known = ", ".join(sorted(area_index())) or "(none)"
            caller.msg(f"No area '|y{slug}|n'. Known areas: {known}.")
            return

        lines = [f"|wRooms in |y{slug}|n:"]
        for room in sorted(rooms, key=lambda r: room_key_of(r) or r.key):
            key = room_key_of(room) or "|x?|n"
            exit_count = len([ex for ex in room.exits if ex.destination])
            lines.append(
                f"  |y{key:<18}|n {room.key} (#{room.id}) — {exit_count} exit(s)"
            )
        caller.msg("\n".join(lines))


def _all_item_prototypes() -> list[dict]:
    """Every item prototype (template) known to the game, in flat form."""
    return [
        _flatten_prototype(p)
        for p in search_prototype()
        if p.get("typeclass") == _ITEM_TYPECLASS
    ]


def _proto_type_label(proto: dict) -> str:
    """A prototype's functional type, with the generic case shown as 'item'."""
    return proto.get("type") or "item"


def _proto_line(proto: dict) -> str:
    """One prototype row: key, display name, and how many copies are in the world."""
    key = proto.get("prototype_key", "?")
    count = len(search_tag(key, category=PROTOTYPE_TAG_CATEGORY))
    return f"  |C{key}|n — {proto.get('key', '?')} ({count} in world)"


def _untemplated_items(protos: list[dict]) -> list:
    """Return live items that are not linked to any known item prototype.

    Items created directly before the prototype workflow was introduced have no
    prototype tag.  A tag can also outlive a deleted prototype, so only a tag
    matching a currently known item prototype counts as a valid link.
    """
    prototype_keys = {proto.get("prototype_key") for proto in protos}
    items = ObjectDB.objects.typeclass_search(_ITEM_TYPECLASS, include_children=True)
    return [
        item
        for item in items
        if not prototype_keys.intersection(
            item.tags.get(category=PROTOTYPE_TAG_CATEGORY, return_list=True)
        )
    ]


def _live_item_type_label(item) -> str:
    """A live item's functional type, with the generic case shown as 'item'."""
    return item.db.type or "item"


def _live_item_line(item) -> str:
    """One untemplated live-item row, identified by an editable dbref."""
    return f"  |C#{item.id}|n — {item.key}"


class CmdItems(Command):
    """
    List item prototypes and any untemplated live items in the game.

    Usage:
      items              every template and untemplated live item, grouped by type
      items <type>       only entries of one type

    Each template row shows its key, display name, and how many linked copies are
    spawned in the world.  Live items created directly (including items predating
    the template workflow) appear separately by #dbref.  Edit a template with
    |wedit item <key>|n or an untemplated item with |wedit #<dbref>|n.  ('item'
    here means an entry with no specialised type.)
    """

    key = "items"
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        caller = self.caller
        arg = self.args.strip().lower()
        valid_types = ("item", *ITEM_TYPES)

        protos = _all_item_prototypes()
        untemplated = _untemplated_items(protos)

        if arg:
            if arg not in valid_types:
                caller.msg(f"Unknown type '{arg}'. Valid: {', '.join(valid_types)}.")
                return
            shown = [p for p in protos if _proto_type_label(p) == arg]
            shown_untemplated = [
                item for item in untemplated if _live_item_type_label(item) == arg
            ]
            if not shown and not shown_untemplated:
                caller.msg(f"No items of type |y{arg}|n.")
                return
            lines = []
            if shown:
                lines.append(f"|wItem templates of type |y{arg}|n ({len(shown)}):")
                lines += [
                    _proto_line(p)
                    for p in sorted(shown, key=lambda d: d.get("prototype_key", ""))
                ]
            if shown_untemplated:
                if lines:
                    lines.append("")
                lines.append(
                    f"|wUntemplated live items of type |y{arg}|n "
                    f"({len(shown_untemplated)}):"
                )
                lines += [
                    _live_item_line(item)
                    for item in sorted(shown_untemplated, key=lambda obj: obj.id)
                ]
                lines.append(
                    "These items were created directly rather than spawned from a "
                    "template; edit one with |wedit #<dbref>|n."
                )
            caller.msg("\n".join(lines))
            return

        groups: dict[str, list] = {}
        for proto in protos:
            groups.setdefault(_proto_type_label(proto), []).append(proto)
        group_order = (*valid_types, *sorted(set(groups).difference(valid_types)))
        lines = []
        if protos:
            lines.append(f"|wItem templates|n ({len(protos)} total):")
            for type_name in group_order:
                group = groups.get(type_name)
                if not group:
                    continue
                lines.append(f"\n|y{type_name}|n ({len(group)}):")
                lines += [
                    _proto_line(p)
                    for p in sorted(group, key=lambda d: d.get("prototype_key", ""))
                ]
        else:
            lines.append(
                "There are no item templates yet. Make one with "
                "|wedit new item <name>|n."
            )

        if untemplated:
            lines.append(f"\n|wUntemplated live items|n ({len(untemplated)} total):")
            untemplated_groups: dict[str, list] = {}
            for item in untemplated:
                untemplated_groups.setdefault(_live_item_type_label(item), []).append(
                    item
                )
            untemplated_group_order = (
                *valid_types,
                *sorted(set(untemplated_groups).difference(valid_types)),
            )
            for type_name in untemplated_group_order:
                group = untemplated_groups.get(type_name)
                if not group:
                    continue
                lines.append(f"\n|y{type_name}|n ({len(group)}):")
                lines += [
                    _live_item_line(item)
                    for item in sorted(group, key=lambda obj: obj.id)
                ]
            lines.append(
                "These items were created directly rather than spawned from a "
                "template; edit one with |wedit #<dbref>|n."
            )
        caller.msg("\n".join(lines))


class CmdNpcs(Command):
    """
    List NPC prototypes.

    Usage:
      npcs

    Each row shows the prototype key, display name, and number of spawned
    copies. Edit a template with |wedit npc <key>|n, create one with
    |wedit new npc <name>|n, or place another copy with |w@spawn <key>|n.
    """

    key = "npcs"
    aliases = ["mobs"]
    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        protos = [
            _flatten_prototype(proto)
            for proto in search_prototype()
            if proto.get("typeclass") == _NPC_TYPECLASS
        ]
        if not protos:
            self.caller.msg(
                "There are no NPC templates yet. Make one with "
                "|wedit new npc <name>|n."
            )
            return

        lines = [f"|wNPC templates|n ({len(protos)} total):"]
        lines += [
            _proto_line(proto)
            for proto in sorted(protos, key=lambda data: data.get("prototype_key", ""))
        ]
        self.caller.msg("\n".join(lines))


# ---------------------------------------------------------------------------
# Mode commands (only present while editing)
# ---------------------------------------------------------------------------


class _BuildCommand(Command):
    """Base for verbs that act on the bound editing target.

    Resolves the target, drops the editing context gracefully if it vanished
    (e.g. the object was deleted), and cancels a pending delete confirmation
    whenever any non-``del`` verb is used.
    """

    locks = _BUILDER_LOCK
    help_category = "Building"
    action_category = ActionCategory.STATE_INDEPENDENT

    @property
    def target(self):
        return self.caller.ndb._build_target

    def at_pre_cmd(self) -> bool:
        if super().at_pre_cmd():
            return True
        if self.caller.ndb._build_target is None:
            self.caller.msg("You are no longer editing anything. Type |wedit here|n.")
            _exit_build_mode(self.caller)
            return True
        if self.key != "del":
            self.caller.ndb._build_del_pending = None
        return False


class CmdBuildShow(_BuildCommand):
    """
    Show the current fields and exits of what you're editing.

    Usage:
      show
    """

    key = "show"

    def func(self) -> None:
        self.caller.msg(_header(self.target) + "\n" + _render_show(self.target))


class CmdBuildFields(_BuildCommand):
    """
    List the editable fields for what you're editing.

    Usage:
      fields
    """

    key = "fields"

    def func(self) -> None:
        self.caller.msg(_render_fields(self.target))


class CmdBuildSet(_BuildCommand):
    """
    Set a field on what you're editing.

    Usage:
      set <field> <value>

    Type |wfields|n to see what you can set.  Values are validated, so a bad
    value is rejected with a reason rather than silently stored.
    """

    key = "set"

    def func(self) -> None:
        caller = self.caller
        schema = _schema(self.target)
        if not schema:
            caller.msg("This object has no editable fields yet.")
            return

        name, _, raw = self.args.strip().partition(" ")
        name = name.lower().strip()
        raw = raw.strip()
        if not name:
            caller.msg("Usage: set <field> <value>")
            return

        field = schema.get(name)
        if field is None:
            caller.msg(f"Unknown field '{name}'. Valid: {', '.join(schema)}.")
            return

        try:
            value = field.validate(raw)
        except ValueError as err:
            caller.msg(f"Invalid value for '{name}': {err}")
            return

        _apply_field(self.target, name, field, value)
        caller.msg(f"Set |y{name}|n to: {value}")
        if field.kind == "type":
            # Changing the type reshapes the editable fields — show the new set.
            caller.msg(_render_show(self.target))


def _get_desc(target) -> str:
    return (
        (target.get("desc") or "") if _is_prototype(target) else (target.db.desc or "")
    )


def _set_desc(target, value) -> None:
    if _is_prototype(target):
        target["desc"] = value
        save_prototype(target)
    else:
        target.db.desc = value


def _desc_load(caller) -> str:
    target = caller.ndb._build_target
    return _get_desc(target) if target else ""


def _desc_save(caller, buf) -> bool:
    target = caller.ndb._build_target
    if target is not None:
        _set_desc(target, buf)
    return True


def _desc_quit(caller) -> None:
    caller.msg("Closed the description editor.")


class CmdBuildDesc(_BuildCommand):
    """
    Edit the description of what you're editing.

    Usage:
      desc              open the multi-line editor
      desc <text>       set the description on one line
    """

    key = "desc"

    def func(self) -> None:
        arg = self.args.strip()
        target = self.target
        if arg:
            _set_desc(target, arg)
            self.caller.msg("Description set.")
            return
        name = target.get("key") if _is_prototype(target) else target.key
        EvEditor(
            self.caller,
            loadfunc=_desc_load,
            savefunc=_desc_save,
            quitfunc=_desc_quit,
            key=f"desc of {name}",
            persistent=False,
        )


class CmdBuildDig(_BuildCommand):
    """
    Dig a new room connected to the room you're editing.

    Usage:
      dig <direction> = <Room Name>
      dig <direction>                 (names the room 'An Unnamed Room')

    Standard directions (north, south, east, west, up, down, in, out, and the
    diagonals) get a two-way exit and short aliases (n, s, e, w, u, d).  The
    room you're editing stays the editing target, and the new room inherits its
    area so it exports together.
    """

    key = "dig"

    def func(self) -> None:
        caller = self.caller
        room = self.target
        if not _is_room(room):
            caller.msg("You can only dig from a room.")
            return

        direction, _, new_name = self.args.partition("=")
        direction = direction.strip().lower()
        new_name = new_name.strip() or "An Unnamed Room"
        if not direction:
            caller.msg("Usage: dig <direction> = <Room Name>")
            return

        new_room = create_object(settings.BASE_ROOM_TYPECLASS, key=new_name)

        # Inherit the current room's area so the pair exports as one unit.
        area_tags = room.tags.get(category="area", return_list=True)
        if area_tags:
            assign_area(new_room, area_tags[0])

        reverse, aliases = _DIRECTIONS.get(direction, (None, []))
        create_object(
            settings.BASE_EXIT_TYPECLASS,
            key=direction,
            aliases=aliases or None,
            location=room,
            destination=new_room,
        )
        if reverse:
            _, reverse_aliases = _DIRECTIONS.get(reverse, (None, []))
            create_object(
                settings.BASE_EXIT_TYPECLASS,
                key=reverse,
                aliases=reverse_aliases or None,
                location=new_room,
                destination=room,
            )
            caller.msg(
                f"Dug |y{new_name}|n ({direction}/{reverse}). Type |wedit "
                f"{direction}|n to edit it, or keep editing |y{room.key}|n."
            )
        else:
            caller.msg(
                f"Dug |y{new_name}|n ({direction}, one-way — '{direction}' is not "
                f"a standard direction). Type |wedit {direction}|n to edit it, or "
                f"keep editing |y{room.key}|n."
            )


class CmdBuildLink(_BuildCommand):
    """
    Add an exit from the room you're editing to an existing room.

    Usage:
      link <direction> = <room name or #dbref>
    """

    key = "link"

    def func(self) -> None:
        caller = self.caller
        room = self.target
        if not _is_room(room):
            caller.msg("You can only link from a room.")
            return

        direction, _, dest_arg = self.args.partition("=")
        direction = direction.strip().lower()
        dest_arg = dest_arg.strip()
        if not direction or not dest_arg:
            caller.msg("Usage: link <direction> = <room>")
            return

        dest = caller.search(dest_arg, global_search=True)
        if not dest:
            return
        if not _is_room(dest):
            caller.msg("You can only link to a room.")
            return
        if any(ex.key == direction and ex.destination == dest for ex in room.exits):
            caller.msg(f"There is already a '{direction}' exit to {dest.key}.")
            return

        _, aliases = _DIRECTIONS.get(direction, (None, []))
        create_object(
            settings.BASE_EXIT_TYPECLASS,
            key=direction,
            aliases=aliases or None,
            location=room,
            destination=dest,
        )
        caller.msg(f"Linked |y{direction}|n -> |y{dest.key}|n.")


class CmdBuildUnlink(_BuildCommand):
    """
    Remove an exit from the room you're editing.

    Usage:
      unlink <direction>
    """

    key = "unlink"

    def func(self) -> None:
        caller = self.caller
        room = self.target
        direction = self.args.strip().lower()
        if not direction:
            caller.msg("Usage: unlink <direction>")
            return

        matches = [
            ex
            for ex in room.exits
            if ex.key == direction or direction in ex.aliases.all()
        ]
        if not matches:
            caller.msg(f"No '{direction}' exit here.")
            return
        for ex in matches:
            ex.delete()
        caller.msg(f"Removed the |y{direction}|n exit.")


class CmdBuildArea(_BuildCommand):
    """
    Assign the room you're editing to an area (used for export).

    Usage:
      area <name>
      area              show the current area
    """

    key = "area"

    def func(self) -> None:
        caller = self.caller
        room = self.target
        if not _is_room(room):
            caller.msg("Only rooms belong to areas.")
            return

        raw = self.args.strip()
        if not raw:
            current = room.tags.get(category="area", return_list=True)
            caller.msg(f"Area: {current[0] if current else '|x(unset)|n'}")
            return

        try:
            slug = as_slug(raw)
        except ValueError as err:
            caller.msg(f"Invalid area name: {err}")
            return
        assign_area(room, slug)
        caller.msg(f"Assigned to area |y{slug}|n.")


class CmdBuildExport(_BuildCommand):
    """
    Export the area of the room you're editing to a git-tracked file.

    Usage:
      export

    Writes game_files/world/areas/<area>.py.  To apply it to the live game,
    sync and reload, then run |wloadarea <area>|n (or restart).
    """

    key = "export"

    def func(self) -> None:
        caller = self.caller
        room = self.target
        if not _is_room(room):
            caller.msg("Only rooms belong to areas.")
            return

        area_tags = room.tags.get(category="area", return_list=True)
        if not area_tags:
            caller.msg("This room has no area yet. Use |warea <name>|n first.")
            return
        area = area_tags[0]

        try:
            path, rooms, exits = export_area(area)
        except OSError:
            logger.log_trace()
            caller.msg("Export failed (could not write the file); see server log.")
            return

        caller.msg(
            f"Exported |y{len(rooms)}|n room(s) and |y{len(exits)}|n exit(s) for "
            f"area |y{area}|n to:\n  {path}"
        )


class CmdBuildDel(_BuildCommand):
    """
    Delete what you're editing.

    Usage:
      del

    Deletion is permanent, so it is two-step: type |wdel|n once to arm it, then
    |wdel|n again to confirm.  Any other verb cancels.  Deleting a room also
    removes its exits.
    """

    key = "del"
    aliases = ["delete"]

    def func(self) -> None:
        caller = self.caller
        target = self.target
        name = target.get("key") if _is_prototype(target) else target.key
        if caller.ndb._build_del_pending is target:
            if _is_prototype(target):
                delete_prototype(target["prototype_key"])
            else:
                if _is_room(target):
                    for ex in list(target.exits):
                        ex.delete()
                target.delete()
            _exit_build_mode(caller)
            caller.msg(f"Deleted |y{name}|n. You are no longer editing.")
        else:
            caller.ndb._build_del_pending = target
            caller.msg(
                f"|rDelete {name}? This cannot be undone.|n "
                "Type |wdel|n again to confirm, or any other verb to cancel."
            )


class CmdBuildDone(_BuildCommand):
    """
    Leave the build editing context.

    Usage:
      done
    """

    key = "done"
    aliases = ["q"]

    def func(self) -> None:
        target = self.target
        # Prototypes persist on every change, so there's nothing to save here.
        name = target.get("key") if _is_prototype(target) else target.key
        _exit_build_mode(self.caller)
        self.caller.msg(f"Done editing |y{name}|n.")


class BuildModeCmdSet(CmdSet):
    """Sticky verbs active only while editing an object via |wbuild|n.

    Priority 10 places these above the default builder commands so that, while
    editing, ``set``/``dig``/``desc`` act on the bound target.  Outside the
    editing context this cmdset is absent and the defaults are untouched.
    """

    key = "BuildMode"
    priority = 10
    mergetype = "Union"

    def at_cmdset_creation(self) -> None:
        self.add(CmdBuildShow())
        self.add(CmdBuildFields())
        self.add(CmdBuildSet())
        self.add(CmdBuildDesc())
        self.add(CmdBuildDig())
        self.add(CmdBuildLink())
        self.add(CmdBuildUnlink())
        self.add(CmdBuildArea())
        self.add(CmdBuildExport())
        self.add(CmdBuildDel())
        self.add(CmdBuildDone())

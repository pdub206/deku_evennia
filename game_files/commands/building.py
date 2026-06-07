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

Phase 1 covers rooms and exits.  NPCs and items slot in by extending
:func:`world.build_schema.schema_for` and adding ``build npc``/``build item``
entry handling — the editing context itself is type-agnostic.
"""

from commands.command import Command
from django.conf import settings
from evennia import CmdSet, create_object
from evennia.utils import logger
from evennia.utils.eveditor import EvEditor
from evennia.utils.utils import inherits_from
from systems.areas import (area_index, assign_area, export_area, load_area,
                           room_key_of, rooms_in_area)
from world.build_schema import as_slug, schema_for

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


def _is_room(obj) -> bool:
    return inherits_from(obj, "evennia.objects.objects.DefaultRoom")


# ---------------------------------------------------------------------------
# Edit-context lifecycle + shared rendering
# ---------------------------------------------------------------------------


def _enter_build_mode(caller, target) -> None:
    """Bind ``target`` and add the sticky build cmdset to ``caller``."""
    caller.ndb._build_target = target
    caller.ndb._build_del_pending = None
    caller.cmdset.add(BuildModeCmdSet, persistent=False)


def _exit_build_mode(caller) -> None:
    """Remove the build cmdset and clear the editing context."""
    caller.cmdset.remove(BuildModeCmdSet)
    caller.ndb._build_target = None
    caller.ndb._build_del_pending = None


def _header(target) -> str:
    return f"|w[build: {target.key} (#{target.id})]|n"


def _field_value(target, name: str, field) -> str:
    """Render the current value of one field for ``show``."""
    if field.kind == "key":
        return target.key
    if field.kind == "attr":
        value = target.attributes.get(field.target or name)
        if value is None:
            return "|x(unset)|n"
        text = str(value)
        return text if len(text) <= 60 else text[:57] + "..."
    if field.kind == "tag":
        tags = target.tags.get(category=field.target or name, return_list=True)
        return ", ".join(tags) if tags else "|x(unset)|n"
    return "?"


def _render_show(target) -> str:
    """A read-out of every field (and, for rooms, exits) of ``target``."""
    schema = schema_for(target)
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
    schema = schema_for(target)
    if not schema:
        return "There are no editable fields for this object yet."
    lines = ["|wEditable fields|n (set with |wset <field> <value>|n):"]
    for name, field in schema.items():
        lines.append(f"  |y{name:<8}|n {field.blurb}")
    return "\n".join(lines)


def _apply_field(target, name: str, field, value) -> None:
    """Write a validated field ``value`` to ``target`` per its schema kind."""
    if field.kind == "key":
        target.key = value
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
      build                 show what you can build and what you're editing
      edit here             edit the room you're standing in
      edit new [<name>]     create a fresh unlinked room and teleport into it
      edit <object>         edit an existing object by name or #dbref

    |wedit new|n is how you start an area "offline": it makes a standalone room
    with no exits and moves you inside, so you can build and review it before
    linking it into the live world.

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

    def func(self) -> None:
        caller = self.caller
        arg = self.args.strip()
        lowered = arg.lower()

        if not arg or lowered == "help":
            self._status()
            return

        if lowered == "new" or lowered.startswith("new "):
            # Everything after the "new" keyword is the (optional) room name.
            self._create_new(arg[len("new") :].strip())
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

    def _create_new(self, name: str) -> None:
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

    def _status(self) -> None:
        caller = self.caller
        target = caller.ndb._build_target
        if target:
            caller.msg(_header(target) + "\n" + _render_show(target))
        else:
            caller.msg(
                "You are not editing anything.\n"
                "  |wedit here|n          edit the current room\n"
                "  |wedit new [<name>]|n  create a fresh unlinked room and go to it\n"
                "  |wedit <object>|n      edit something by name or #dbref\n"
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

    def func(self) -> None:
        self.caller.msg(_render_area_index())


class CmdRooms(Command):
    """
    List rooms, optionally within a single area.

    Usage:
      rooms              show all areas and their room counts
      rooms <area>       list the rooms in one area

    Each room is shown by its area key, display name, and #dbref so you can
    jump straight to it with |wedit <name>|n or |wedit #<dbref>|n.
    """

    key = "rooms"
    locks = _BUILDER_LOCK
    help_category = "Building"

    def func(self) -> None:
        caller = self.caller
        arg = self.args.strip()
        if not arg:
            caller.msg(_render_area_index())
            return

        try:
            slug = as_slug(arg)
        except ValueError as err:
            caller.msg(f"Invalid area name: {err}")
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
        schema = schema_for(self.target)
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


def _desc_load(caller) -> str:
    target = caller.ndb._build_target
    return (target.db.desc or "") if target else ""


def _desc_save(caller, buf) -> bool:
    target = caller.ndb._build_target
    if target:
        target.db.desc = buf
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
        if arg:
            self.target.db.desc = arg
            self.caller.msg("Description set.")
            return
        EvEditor(
            self.caller,
            loadfunc=_desc_load,
            savefunc=_desc_save,
            quitfunc=_desc_quit,
            key=f"desc of {self.target.key}",
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
        if caller.ndb._build_del_pending is target:
            name = target.key
            if _is_room(target):
                for ex in list(target.exits):
                    ex.delete()
            target.delete()
            _exit_build_mode(caller)
            caller.msg(f"Deleted |y{name}|n. You are no longer editing.")
        else:
            caller.ndb._build_del_pending = target
            caller.msg(
                f"|rDelete {target.key}? This cannot be undone.|n "
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
        name = self.target.key
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

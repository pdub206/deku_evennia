"""
Overrides of common Evennia game commands (look, pose, etc.).

These attach game-specific policy or presentation without reimplementing the
underlying Evennia command logic.
"""

from typing import Any
from uuid import uuid4

from commands.command import Command, MuxCommand
from evennia.commands.default.general import CmdAccess as _BaseAccess
from evennia.commands.default.general import CmdDrop as _BaseDrop
from evennia.commands.default.general import CmdGet as _BaseGet
from evennia.commands.default.general import CmdGive as _BaseGive
from evennia.commands.default.general import CmdInventory as _BaseInventory
from evennia.commands.default.general import CmdLook as _BaseLook
from evennia.commands.default.general import CmdNick as _BaseNick
from evennia.commands.default.general import CmdPose as _BasePose
from evennia.commands.default.general import CmdSetDesc as _BaseSetDesc
from evennia.commands.default.help import CmdHelp as _BaseHelp
from evennia.utils import utils
from systems.action_policy import ActionCategory
from systems.action_queue import (
    ActionQueueError,
    action_audit,
    cancel_action,
    inspect_action,
    repair_action,
)
from systems.containers import (
    can_access_contents,
    container_is_open,
    container_is_transparent,
)
from systems.corpses import (
    CorpseError,
    inspect_corpse,
    withdraw,
    transfer_currency,
    withdraw_many,
)
from systems.currency import (
    CurrencyError,
    balance,
    create_pile,
    debit,
    format_coins,
    pickup_pile,
    transfer,
)
from systems.dice import roll
from systems.doors import DoorError, door_state
from systems.encumbrance import can_receive, character_load
from systems.equipment import (
    WEAR_LOCATIONS,
    WEAR_SIDES,
    EquipmentError,
    allowed_wear_locations,
    wear_phrase,
)
from systems.recall import schedule_recall
from systems.room_roles import RoomRoleError, validate_room_roles
from systems.visibility import (
    active_search,
    active_search_extras,
    discover_passively,
    matching_extra_descriptions,
    room_visibility,
    target_visibility,
)


class CmdLook(_BaseLook):
    """
    Look at the room or an object.

    Usage:
      look
      look <object>
      look <direction>

    Requires a conscious character able to observe their surroundings.
    """

    action_category = ActionCategory.OBSERVE

    def func(self) -> None:
        """Apply darkness, direction, and general container inspection rules."""
        caller = self.caller
        room = caller.location
        if room is not None and not room_visibility(caller, room).visible:
            self.msg("It is too dark to see.")
            return
        query = self.args.strip()
        if query and not query.casefold().startswith("in "):
            owners = [room] + list(room.filter_visible(room.contents, caller))
            details = matching_extra_descriptions(caller, query, owners)
            if len(details) == 1:
                self.msg(details[0][2]["description"])
                return
            if len(details) > 1:
                self.msg("You do not see one clear detail by that name.")
                return
            exits = [
                exit_obj
                for exit_obj in getattr(room, "exits", ())
                if exit_obj.key.casefold() == query.casefold()
            ]
            if len(exits) == 1:
                exit_obj = exits[0]
                if not target_visibility(caller, exit_obj).visible:
                    self.msg("You do not see that here.")
                    return
                lines = [exit_obj.get_display_name(caller)]
                description = exit_obj.attributes.get("desc")
                if description:
                    lines.append(description)
                destination = exit_obj.destination
                if (
                    destination is not None
                    and room_visibility(caller, destination, adjacent=True).visible
                ):
                    from systems.room_policy import room_policy

                    try:
                        private = room_policy(destination).private
                    except (TypeError, ValueError):
                        private = True
                    if not private:
                        lines.extend(
                            (
                                destination.get_display_name(caller),
                                destination.attributes.get("desc") or "",
                            )
                        )
                self.msg("\n".join(line for line in lines if line))
                return
        prefix = "in "
        if not query.casefold().startswith(prefix):
            super().func()
            return
        target_name = query[len(prefix) :].strip()
        if not target_name:
            self.msg("Look in what?")
            return
        target = self.caller.search(target_name, location=self.caller.location)
        if not target:
            return
        if target.is_typeclass("typeclasses.objects.Corpse", exact=False):
            try:
                contents = inspect_corpse(target, self.caller)
            except CorpseError:
                self.msg("That corpse cannot be inspected right now.")
                return
            if not contents:
                self.msg(
                    f"The corpse of {target.key.removeprefix('corpse of ')} is empty."
                )
                return
            lines = [item.get_display_name(self.caller) for item in contents]
            self.msg(
                f"Inside {target.key}:\n" + "\n".join(f"  {line}" for line in lines)
            )
            return
        if str(target.attributes.get("type") or "").casefold() != "container":
            self.msg("You cannot look inside that.")
            return
        try:
            door_state(target)
        except DoorError:
            self.msg("That container cannot be inspected right now.")
            return
        if not container_is_open(target) and not container_is_transparent(target):
            self.msg("That container is closed.")
            return
        if not target.access(caller, "view", default=True):
            self.msg("You cannot inspect that container.")
            return
        contents = sorted(target.contents, key=lambda obj: (obj.key.casefold(), obj.id))
        if not contents:
            self.msg(f"{target.get_display_name(caller)} is empty.")
            return
        lines = [item.get_display_name(self.caller) for item in contents]
        self.msg(f"Inside {target.key}:\n" + "\n".join(f"  {line}" for line in lines))


class CmdExits(Command):
    """List visible local exits and public door state. Usage: exits"""

    key = "exits"
    aliases = ("obvious exits",)
    action_category = ActionCategory.OBSERVE

    def func(self) -> None:
        """Render only exits the caller can currently perceive."""
        room = self.caller.location
        if room is None or not room_visibility(self.caller, room).visible:
            self.msg("You cannot make out any exits.")
            return
        passive = set(discover_passively(self.caller, room.exits))
        lines = []
        for exit_obj in sorted(
            room.exits, key=lambda obj: (obj.key.casefold(), obj.id)
        ):
            if (
                exit_obj not in passive
                and not target_visibility(self.caller, exit_obj).visible
            ):
                continue
            label = exit_obj.get_display_name(self.caller)
            try:
                state = door_state(exit_obj)
            except DoorError:
                continue
            if state is not None:
                label += " (open)" if state.open else " (closed)"
            lines.append(label)
        self.msg(
            "Obvious exits:\n"
            + ("\n".join(f"  {line}" for line in lines) if lines else "  None.")
        )


class CmdSearch(Command):
    """Actively search for hidden exits. Usage: search [direction]"""

    key = "search"
    action_category = ActionCategory.OBSERVE

    def func(self) -> None:
        """Roll separately for every matching eligible hidden exit."""
        room = self.caller.location
        if room is None:
            self.msg("There is nowhere to search.")
            return
        query = self.args.strip().casefold()
        candidates = [
            exit_obj
            for exit_obj in room.exits
            if not query or exit_obj.key.casefold().startswith(query)
        ]
        found = active_search(self.caller, candidates, roller=roll)
        owners = [room] + list(room.filter_visible(room.contents, self.caller))
        extra_found = active_search_extras(
            self.caller, owners, query=query, roller=roll
        )
        names = [obj.get_display_name(self.caller) for obj in found]
        names.extend(record["keywords"][0] for _owner, _index, record in extra_found)
        if names:
            self.msg("You discover: " + ", ".join(names) + ".")
        else:
            self.msg("You find nothing hidden.")


class CmdExamine(Command):
    """Inspect public details about one visible local target. Usage: examine <target>"""

    key = "examine"
    aliases = ("exam", "exa")
    action_category = ActionCategory.OBSERVE

    def func(self) -> None:
        """Show descriptions and public physical state without private statistics."""
        if not self.args.strip():
            self.msg("Examine what?")
            return
        candidates = list(self.caller.location.contents) + list(self.caller.contents)
        matches = self.caller.search(
            self.args.strip(), candidates=candidates, quiet=True, use_locks=False
        )
        visible = [
            obj
            for obj in matches
            if obj is self.caller
            or obj.location is self.caller
            or target_visibility(self.caller, obj).visible
        ]
        if len(visible) != 1:
            self.msg("You do not see one clear target by that name.")
            return
        target = visible[0]
        lines = [
            target.get_display_name(self.caller),
            target.get_display_desc(self.caller),
        ]
        item_type = target.attributes.get("type")
        if item_type:
            lines.append(f"Type: {item_type}.")
        weight = target.attributes.get("weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            lines.append(f"Weight: {weight:g} lb.")
        if str(item_type).casefold() == "container":
            try:
                state = door_state(target)
            except DoorError:
                state = None
            lines.append(
                "Container: " + ("open." if state and state.open else "closed.")
            )
        if hasattr(target, "action_position"):
            lines.append(f"Posture: {target.action_position.value}.")
            equipment = target.get_display_things(self.caller)
            if equipment:
                lines.append(equipment)
            from systems.combat import get_target, is_fighting

            if is_fighting(target):
                opponent = get_target(target)
                if (
                    opponent is not None
                    and target_visibility(self.caller, opponent).visible
                ):
                    lines.append(f"Fighting: {opponent.get_display_name(self.caller)}.")
                else:
                    lines.append("Fighting: yes.")
        self.msg("\n".join(line for line in lines if line))


class CmdRecall(MuxCommand):
    """Begin recalling to the configured safe destination.

    Usage:
      recall
      home
    """

    key = "recall"
    aliases = ("home",)
    action_category = ActionCategory.MOVE

    def func(self) -> None:
        """Queue one delayed recall without consulting Object.home."""
        result = schedule_recall(self.caller)
        if result.status.value in {"queued", "replaced"}:
            self.msg("You begin recalling to safety.")
        else:
            self.msg(f"You cannot recall right now ({result.reason}).")


# Compatibility import for tests and extensions; the command key is now recall.
CmdHome = CmdRecall


class CmdActionQueue(MuxCommand):
    """Inspect or recover durable delayed actions.

    Usage:
      actionqueue [<character>]
      actionqueue/cancel <character>
      actionqueue/repair <character>
    """

    key = "actionqueue"
    aliases = ("actions",)
    locks = "cmd:perm(Builder)"
    switch_options = ("cancel", "repair")
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Show bounded state or perform an explicit staff recovery operation."""
        target = self.caller
        if self.args:
            target = self.caller.search(self.args, global_search=True)
            if not target:
                return
        if "cancel" in self.switches:
            result = cancel_action(target, reason="staff_cancel")
            self.msg(f"Action queue: {result.status.value} ({result.reason}).")
            return
        if "repair" in self.switches:
            result = repair_action(target)
            self.msg(f"Action queue: {result.status.value} ({result.reason}).")
            return
        try:
            active = inspect_action(target)
            history = action_audit(target)
        except ActionQueueError as err:
            self.msg(f"Action queue state needs repair: {err}")
            return
        if active is None:
            self.msg(
                f"{target.key} has no queued action. Audit entries: {len(history)}."
            )
            return
        self.msg(
            f"{target.key}: {active['definition']} [{active['status']}] "
            f"due {active['due_token']}; audit entries: {len(history)}."
        )


class CmdRoomRoles(MuxCommand):
    """Validate the configured start, respawn, and recall room roles."""

    key = "roomroles"
    aliases = ("roomrolecheck",)
    locks = "cmd:perm(Builder)"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Report all role references only when every role resolves uniquely."""
        try:
            roles = validate_room_roles()
        except RoomRoleError as err:
            self.msg(f"Room-role validation failed: {err}")
            return
        self.msg(
            "Room roles valid:\n"
            + "\n".join(f"  {role.setting} = {role.reference}" for role in roles)
        )


class CmdNick(_BaseNick):
    """Manage personal input aliases regardless of character position."""

    action_category = ActionCategory.STATE_INDEPENDENT


class CmdSetDesc(_BaseSetDesc):
    """Change a character description as an in-world manipulation action."""

    action_category = ActionCategory.MANIPULATE


class CmdAccess(_BaseAccess):
    """Show account and character permissions in every effective state."""

    action_category = ActionCategory.STATE_INDEPENDENT


class CmdHelp(_BaseHelp):
    """Keep help available as a state-independent recovery command."""

    action_category = ActionCategory.STATE_INDEPENDENT


class CmdPose(_BasePose):
    """
    Pose or emote an action.

    Usage:
      pose <action>
      :<action>

    Requires a conscious character able to communicate.
    """

    action_category = ActionCategory.COMMUNICATE


class CmdInventory(_BaseInventory):
    """
    View your inventory.

    Usage:
      inventory
      inv

    Lists what you are carrying by name only. Use |wlook <item>|n to read an
    item's description. Requires a position that permits item handling.
    """

    action_category = ActionCategory.MANIPULATE

    def func(self) -> None:
        caller = self.caller
        items = caller.contents
        if not items:
            caller.msg(text=("You are not carrying anything.", {"type": "inventory"}))
            return
        # Group visibly identical items into stacks (e.g. "two torches"), but
        # show only the name — descriptions are for `look <item>`, not the list.
        lines = [
            f"  |C{name}|n"
            for name, _desc, _objs in utils.group_objects_by_key_and_desc(
                items, caller=caller
            )
        ]
        load = character_load(caller)
        string = "|wYou are carrying:|n\n" + "\n".join(lines)
        string += (
            f"\n|wLoad:|n {load.count}/{load.count_limit} items, "
            f"{load.weight:g}/{load.weight_limit:g} lb."
        )
        if load.overloaded:
            string += " |r(OVERLOADED)|n"
        caller.msg(text=(string, {"type": "inventory"}))


class CmdGet(_BaseGet):
    """Pick up an item when the shared action policy allows manipulation.

    Usage:
      get <item>
      get <item|all> [from] <container>
    """

    action_category = ActionCategory.MANIPULATE

    def func(self) -> None:
        """Preflight every selected object so a batch never partially picks up."""
        source = self._parse_container_source()
        if source:
            item_name, container = source
            if not item_name or container is None:
                return
            if container.is_typeclass("typeclasses.objects.Corpse", exact=False):
                self._get_from_corpse(item_name, container)
            else:
                self._get_from_container(item_name, container)
            return
        caller = self.caller
        if not self.args:
            self.msg("Get what?")
            return
        if self.args.strip().casefold() == "all":
            objs = [
                obj
                for obj in caller.location.contents
                if obj is not caller
                and not obj.destination
                and obj.access(caller, "get")
                and not obj.is_typeclass("typeclasses.objects.Corpse", exact=False)
            ]
            if not objs:
                self.msg("There is nothing here you can pick up.")
                return
            self._move_container_batch(objs, caller, caller.location, "pick up", "in")
            return
        objs = caller.search(self.args, location=caller.location, stacked=self.number)
        if not objs:
            return
        objs = utils.make_iter(objs)
        if (
            len(objs) == 1
            and str(objs[0].attributes.get("type") or "").casefold() == "money"
        ):
            pile = objs[0]
            try:
                result = pickup_pile(pile, caller, f"pickup:{pile.id}")
            except CurrencyError:
                self.msg("Those coins cannot be picked up right now.")
                return
            if not result.success:
                self.msg("You cannot carry any more coins.")
                return
            caller.location.msg_contents(
                f"$You() $conj(pick) up {format_coins(result.amount)}.", from_obj=caller
            )
            return
        if len(objs) == 1 and caller == objs[0]:
            self.msg("You can't get yourself.")
            return
        for obj in objs:
            if not obj.access(caller, "get"):
                self.msg(obj.db.get_err_msg or "You can't get that.")
                return
            if not obj.at_pre_get(caller):
                return
        result = can_receive(caller, objs)
        if not result.allowed:
            caller.msg(result.message)
            return
        moved = []
        sources = {obj: obj.location for obj in objs}
        for obj in objs:
            if obj.move_to(caller, quiet=True, move_type="get", capacity_actor=caller):
                moved.append(obj)
                obj.at_get(caller)
            else:
                for moved_obj in moved:
                    moved_obj.move_to(
                        sources[moved_obj],
                        quiet=True,
                        move_type="rollback",
                        encumbrance_bypass="batch get rollback",
                    )
                self.msg("That can't be picked up.")
                return
        obj_name = moved[0].get_numbered_name(len(moved), caller, return_string=True)
        caller.location.msg_contents(
            f"$You() $conj(pick) up {obj_name}.", from_obj=caller
        )

    def _parse_container_source(self) -> tuple[str, Any] | None:
        """Resolve explicit or shorthand container grammar without guessing."""
        args = self.args.strip()
        lowered = args.casefold()
        candidates = self._reachable_objects()
        if " from " in lowered:
            index = lowered.index(" from ")
            item_name, source_name = args[:index].strip(), args[index + 6 :].strip()
            if not item_name or not source_name:
                self.msg("Usage: get <item|all> [from] <container>")
                return ("", None)
            source = self.caller.search(
                source_name,
                candidates=candidates,
                use_locks=False,
            )
            return (item_name, source) if source else ("", None)
        words = args.split()
        matches = []
        for index in range(1, len(words)):
            container_name = " ".join(words[index:])
            found = self.caller.search(
                container_name,
                candidates=candidates,
                quiet=True,
                use_locks=False,
            )
            if not found:
                found = [
                    obj
                    for obj in candidates
                    if obj.key.casefold().endswith(container_name.casefold())
                ]
            for obj in found:
                if str(
                    obj.attributes.get("type") or ""
                ).casefold() == "container" or obj.is_typeclass(
                    "typeclasses.objects.Corpse", exact=False
                ):
                    matches.append((" ".join(words[:index]), obj))
        by_container = {}
        for item, obj in matches:
            current = by_container.get(obj.id)
            if current is None or len(item) < len(current[0]):
                by_container[obj.id] = (item, obj)
        return next(iter(by_container.values())) if len(by_container) == 1 else None

    def _reachable_objects(self) -> list[Any]:
        """Return direct objects plus descendants of open reachable containers."""
        found = list(self.caller.contents) + list(self.caller.location.contents)
        pending = list(found)
        seen = {obj.id for obj in found}
        while pending:
            parent = pending.pop()
            if not container_is_open(parent):
                continue
            for child in parent.contents:
                if child.id in seen:
                    continue
                seen.add(child.id)
                found.append(child)
                pending.append(child)
        return found

    def _get_from_corpse(self, item_name: str, corpse: Any) -> None:
        """Withdraw a selected item or every eligible item from one corpse."""
        if not item_name or corpse is None:
            return
        caller = self.caller
        if item_name.casefold() in {"coin", "coins", "money"}:
            try:
                amount = transfer_currency(corpse, caller)
            except CorpseError as err:
                self.msg(str(err))
                return
            self.msg(
                f"You take {format_coins(amount)} from {corpse.key}."
                if amount
                else "That corpse has no coins."
            )
            return
        if item_name.casefold() == "all":
            self._get_all_from_corpse(corpse)
            return
        item = caller.search(
            item_name,
            location=corpse,
            stacked=self.number,
            nofound_string=f"There is no {item_name} in that corpse.",
            multimatch_string=f"There is more than one {item_name} in that corpse:",
        )
        if not item:
            return
        if isinstance(item, (list, tuple)):
            self.msg("Choose one item to remove from the corpse.")
            return
        try:
            result = withdraw(corpse, caller, item)
        except CorpseError:
            self.msg("That corpse cannot be looted right now.")
            return
        if not result.moved:
            self.msg(result.message)
            return
        caller.location.msg_contents(
            f"$You() $conj(take) {item.get_display_name(caller)} from {corpse.key}.",
            from_obj=caller,
        )

    def _get_all_from_corpse(self, corpse: Any) -> None:
        """Report every independent bulk-loot outcome in deterministic order."""
        try:
            results = withdraw_many(corpse, self.caller)
            amount = transfer_currency(corpse, self.caller)
        except CorpseError:
            self.msg("That corpse cannot be looted right now.")
            return
        moved = [result.item for result in results if result.moved]
        failed = [result for result in results if not result.moved]
        if moved:
            names = ", ".join(item.get_display_name(self.caller) for item in moved)
            self.msg(f"You take: {names}.")
            self.caller.location.msg_contents(
                f"$You() $conj(search) {corpse.key}.", from_obj=self.caller
            )
        if amount:
            self.msg(f"You take {format_coins(amount)}.")
        if failed:
            reasons = " ".join(
                f"{result.item.get_display_name(self.caller)}: {result.message}"
                for result in failed
            )
            self.msg(f"Left behind — {reasons}")
        if not results and not amount:
            self.msg("That corpse is empty.")

    def _get_from_container(self, item_name: str, container: Any) -> None:
        """Move one or all selected contents atomically into the caller."""
        if not item_name or container is None:
            return
        if not can_access_contents(container, self.caller, insert=False):
            self.msg("That container is closed or inaccessible.")
            return
        objs = (
            list(container.contents)
            if item_name.casefold() == "all"
            else utils.make_iter(
                self.caller.search(item_name, location=container, stacked=self.number)
            )
        )
        if not objs:
            self.msg(
                "That container is empty."
                if item_name.casefold() == "all"
                else "You do not find that inside."
            )
            return
        self._move_container_batch(objs, self.caller, container, "take", "from")

    def _move_container_batch(
        self, objs: list[Any], destination: Any, other: Any, verb: str, preposition: str
    ) -> None:
        """Preflight and move a batch, rolling every committed move back on failure."""
        taking = destination is self.caller
        if taking:
            for obj in objs:
                if not obj.access(self.caller, "get") or not obj.at_pre_get(
                    self.caller
                ):
                    return
        result = can_receive(destination, objs)
        if not result.allowed:
            self.msg(result.message)
            return
        sources = {obj: obj.location for obj in objs}
        moved = []
        for obj in objs:
            if not obj.move_to(destination, quiet=True, capacity_actor=self.caller):
                for prior in moved:
                    prior.move_to(
                        sources[prior],
                        quiet=True,
                        encumbrance_bypass="container batch rollback",
                    )
                self.msg("That transfer could not be completed.")
                return
            moved.append(obj)
        for obj in moved:
            obj.at_get(self.caller) if taking else obj.at_drop(self.caller)
        names = ", ".join(obj.get_display_name(self.caller) for obj in moved)
        self.caller.location.msg_contents(
            f"$You() $conj({verb}) {names} {preposition} {other.get_display_name(self.caller)}.",
            from_obj=self.caller,
        )


class CmdPut(CmdGet):
    """Put carried items into an open container.

    Usage: put <item|all> [in] <container>
    """

    key = "put"

    def func(self) -> None:
        """Resolve either grammar and atomically insert the selected items."""
        parsed = self._parse_put()
        if not parsed:
            self.msg("Usage: put <item|all> [in] <container>")
            return
        item_name, container = parsed
        if container.is_typeclass("typeclasses.objects.Corpse", exact=False):
            self.msg("You cannot put anything into a corpse.")
            return
        if not can_access_contents(container, self.caller, insert=True):
            self.msg("That container is closed or inaccessible.")
            return
        ancestors = set()
        current = container
        while current is not None:
            ancestors.add(current)
            current = current.location
        objs = (
            [obj for obj in self.caller.contents if obj not in ancestors]
            if item_name.casefold() == "all"
            else utils.make_iter(
                self.caller.search(item_name, location=self.caller, stacked=self.number)
            )
        )
        if not objs:
            self.msg("You have nothing to put there.")
            return
        for obj in objs:
            if not obj.access(self.caller, "drop") or not obj.at_pre_drop(self.caller):
                return
        self._move_container_batch(objs, container, container, "put", "in")

    def _parse_put(self) -> tuple[str, Any] | None:
        """Resolve an inventory/room container with an optional ``in`` keyword."""
        original = self.args
        lowered = original.casefold()
        if " in " in lowered:
            index = lowered.index(" in ")
            self.args = original[:index] + " from " + original[index + 4 :]
        result = self._parse_container_source()
        self.args = original
        return result


class CmdFastHands(CmdGet):
    """Use Fast Hands to pick up one nearby item during combat.

    Usage:
      fastget <item>
      fast <item>
    """

    key = "fastget"
    aliases = ["fast"]
    help_category = "Combat"
    action_category = ActionCategory.COMBAT

    def func(self) -> None:
        """Reuse ordinary pickup validation and accelerate only a successful use."""
        from systems.class_features import has_granted_feature
        from systems.combat import accelerate_next_action, is_fighting

        if not has_granted_feature(self.caller, "rogue.fast_hands"):
            self.msg("You have not learned Fast Hands.")
            return
        if not is_fighting(self.caller):
            self.msg("Use get outside combat.")
            return
        if " from " in self.args.casefold() or (self.number or 0) > 1:
            self.msg("Fast Hands can pick up one nearby item at a time.")
            return
        before = {item.id for item in self.caller.contents}
        super().func()
        after = {item.id for item in self.caller.contents}
        if after - before:
            accelerate_next_action(self.caller)


class CmdDrop(_BaseDrop):
    """Drop a carried item when the shared action policy allows manipulation.

    Usage:
      drop <item>
    """

    action_category = ActionCategory.MANIPULATE

    def func(self) -> None:
        """Drop an item, or turn wallet currency into one physical pile."""
        words = self.args.strip().split()
        parsed_amount = self.number if len(words) == 1 else None
        if (len(words) == 2 and words[1].casefold() in {"coin", "coins"}) or (
            parsed_amount and words[0].casefold() in {"coin", "coins"}
        ):
            try:
                amount = int(parsed_amount or words[0])
                transaction_id = f"drop:{self.caller.id}:{uuid4()}"
                result = debit(
                    self.caller,
                    amount,
                    transaction_id,
                    actor=self.caller,
                    source="command:drop",
                    reason="physical money pile",
                )
            except (CurrencyError, ValueError):
                self.msg("Usage: drop <positive amount> coins")
                return
            if not result.success:
                self.msg("You do not have that many coins.")
                return
            try:
                create_pile(self.caller.location, amount, transaction_id)
            except Exception:
                from systems.currency import credit

                credit(
                    self.caller,
                    amount,
                    f"rollback:{transaction_id}",
                    actor=self.caller,
                    source="command:drop",
                    reason="pile creation rollback",
                )
                self.msg("You cannot drop those coins right now.")
                return
            self.caller.location.msg_contents(
                f"$You() $conj(drop) {format_coins(amount)}.", from_obj=self.caller
            )
            return
        super().func()


class CmdGive(_BaseGive):
    """Give an item when the shared action policy allows manipulation.

    Usage:
      give <item> <character>
      give <amount> coins <character>
    """

    action_category = ActionCategory.MANIPULATE

    def func(self) -> None:
        """Preflight aggregate giving so the recipient gets all objects or none."""
        caller = self.caller
        if not self.args:
            caller.msg(
                "Usage: give <item> <character> or give <amount> coins <character>"
            )
            return
        parsed = self._parse_recipient()
        if not parsed:
            caller.msg(
                "Usage: give <item> <character> or give <amount> coins <character>"
            )
            return
        item_name, target = parsed
        words = item_name.split()
        if (len(words) == 2 and words[1].casefold() in {"coin", "coins"}) or (
            self.number and item_name.casefold() in {"coin", "coins"}
        ):
            self._give_coins(str(self.number or words[0]), target)
            return
        to_give = caller.search(
            item_name,
            location=caller,
            nofound_string=f"You aren't carrying {item_name}.",
            multimatch_string=f"You carry more than one {item_name}:",
            stacked=self.number,
        )
        if not to_give:
            return
        to_give = utils.make_iter(to_give)
        singular, plural = to_give[0].get_numbered_name(len(to_give), caller)
        if target == caller:
            caller.msg(
                f"You keep {plural if len(to_give) > 1 else singular} to yourself."
            )
            return
        for obj in to_give:
            if not obj.at_pre_give(caller, target):
                return
        result = can_receive(target, to_give)
        if not result.allowed:
            caller.msg(result.message)
            return
        moved = []
        sources = {obj: obj.location for obj in to_give}
        for obj in to_give:
            if obj.move_to(target, quiet=True, move_type="give", capacity_actor=caller):
                moved.append(obj)
                obj.at_give(caller, target)
            else:
                for moved_obj in moved:
                    moved_obj.move_to(
                        sources[moved_obj],
                        quiet=True,
                        move_type="rollback",
                        encumbrance_bypass="batch give rollback",
                    )
                caller.msg(
                    f"You could not give that to {target.get_display_name(caller)}."
                )
                return
        obj_name = to_give[0].get_numbered_name(len(moved), caller, return_string=True)
        caller.msg(f"You give {obj_name} to {target.get_display_name(caller)}.")
        target.msg(f"{caller.get_display_name(target)} gives you {obj_name}.")

    def _parse_recipient(self) -> tuple[str, Any] | None:
        """Resolve a trailing, visible room character without required separators."""
        args = self.args.strip()
        if self.rhs:
            target = self.caller.search(self.rhs, location=self.caller.location)
            return (self.lhs.strip(), target) if target else None
        words = args.split()
        for index in range(1, len(words)):
            target_name = " ".join(words[index:])
            matches = self.caller.search(
                target_name, location=self.caller.location, quiet=True
            )
            characters = [
                obj
                for obj in matches
                if obj.is_typeclass("typeclasses.characters.Character", exact=False)
            ]
            if len(characters) == 1:
                return " ".join(words[:index]), characters[0]
        return None

    def _give_coins(self, raw_amount: str, target: Any) -> None:
        """Transfer wallet funds with one private message per participant."""
        try:
            amount = int(raw_amount)
            result = transfer(
                self.caller,
                target,
                amount,
                f"give:{self.caller.id}:{target.id}:{uuid4()}",
                actor=self.caller,
                source="command:give",
                reason="player transfer",
            )
        except (CurrencyError, ValueError) as err:
            self.msg(str(err))
            return
        if not result.success:
            self.msg(
                "You do not have that many coins."
                if result.outcome == "insufficient funds"
                else "That character cannot receive any more coins."
            )
            return
        amount_text = format_coins(amount)
        self.msg(f"You give {amount_text} to {target.get_display_name(self.caller)}.")
        target.msg(f"{self.caller.get_display_name(target)} gives you {amount_text}.")


class CmdCoins(Command):
    """Display the caller's weightless wallet currency."""

    key = "coins"
    aliases = ["wealth"]
    help_category = "Items"

    def func(self) -> None:
        """Show only the caller's validated balance."""
        try:
            self.msg(f"You have {format_coins(balance(self.caller))}.")
        except CurrencyError:
            self.msg("Your wallet needs staff attention.")


class CmdCurrency(MuxCommand):
    """Inspect or correct wallets through audited Builder-only operations."""

    key = "currency"
    locks = "cmd:perm(Builder)"
    help_category = "Builder"

    def func(self) -> None:
        """Run inspect, grant, remove, or repair with explicit provenance."""
        from systems.currency import audit_entries, credit, repair

        operation = next(iter(self.switches), "inspect").casefold()
        if operation == "inspect":
            target = self.caller.search(self.args.strip(), global_search=True)
            if not target:
                return
            try:
                value = balance(target)
            except CurrencyError as err:
                value = f"invalid ({err})"
            self.msg(
                f"{target.key}: {value}; ledger entries: {len(audit_entries(target))}."
            )
            return
        words = self.args.strip().split(maxsplit=3)
        if operation not in {"grant", "remove", "repair"} or len(words) != 4:
            self.msg(
                "Usage: currency/<grant|remove|repair> <amount> <target> <source> <reason>"
            )
            return
        raw_amount, target_name, source, reason = words
        target = self.caller.search(target_name, global_search=True)
        if not target:
            return
        try:
            amount = int(raw_amount)
            function = {"grant": credit, "remove": debit, "repair": repair}[operation]
            result = function(
                target,
                amount,
                f"staff:{source}",
                actor=self.caller,
                source=source,
                reason=reason,
            )
        except (CurrencyError, ValueError) as err:
            self.msg(str(err))
            return
        self.msg(
            f"{operation.title()} {target.key}: {result.outcome}; balance {result.after}."
        )


class CmdJunk(Command):
    """
    Permanently discard a carried item.

    Usage:
      junk <item>

    The selected item instance disappears from the game. Its prototype remains
    available, so builders can continue spawning new copies.
    """

    key = "junk"
    locks = "cmd:all()"
    help_category = "Items"

    def func(self) -> None:
        """Find an item in the caller's inventory and delete only that instance."""
        caller = self.caller
        item_name = self.args.strip()
        if not item_name:
            caller.msg("Junk what?")
            return

        item = caller.search(
            item_name,
            location=caller,
            nofound_string=f"You aren't carrying {item_name}.",
            multimatch_string=f"You carry more than one {item_name}:",
        )
        if not item:
            return
        if not utils.inherits_from(item, "typeclasses.objects.Item"):
            caller.msg("You can only junk items.")
            return

        display_name = item.get_display_name(caller)
        if not item.delete():
            caller.msg(f"You cannot junk {display_name}.")
            return

        caller.msg(f"You junk {display_name}.")


class CmdWear(Command):
    """
    Wear or ready a carried item in one of its allowed equipment slots.

    Usage:
      wear <item>
      wear <item> <location>

    Items that fit both sides, such as rings and wristwear, require the side when
    both are open. If only one allowed slot is open, it is chosen automatically.
    """

    key = "wear"
    locks = "cmd:all()"
    help_category = "Items"

    def parse(self) -> None:
        """Separate an optional side or exact wear location from the item name."""
        self.raw_item_name = self.args.strip()
        self.item_name = self.raw_item_name
        self.location_selector = None
        selectors = sorted((*WEAR_LOCATIONS, *WEAR_SIDES), key=len, reverse=True)
        lowered = self.raw_item_name.lower()
        for selector in selectors:
            suffix = f" {selector}"
            if lowered.endswith(suffix):
                self.item_name = self.raw_item_name[: -len(suffix)].strip()
                self.location_selector = selector
                break

    def func(self) -> None:
        """Validate the item and equip it in an available allowed slot."""
        caller = self.caller
        if not self.item_name:
            caller.msg("Wear what?")
            return

        # Prefer an exact carried-object name before treating a trailing word as
        # a location, so an item actually named "iron shield" remains wearable.
        full_name_matches = caller.search(
            self.raw_item_name, location=caller, quiet=True, exact=True
        )
        if len(full_name_matches) == 1:
            item = full_name_matches[0]
            self.location_selector = None
        else:
            item = caller.search(
                self.item_name,
                location=caller,
                nofound_string=f"You aren't carrying {self.item_name}.",
                multimatch_string=f"You carry more than one {self.item_name}:",
            )
        if not item:
            return
        if not utils.inherits_from(item, "typeclasses.objects.Item"):
            caller.msg("You can only wear items.")
            return

        locations = list(allowed_wear_locations(item))
        display_name = item.get_display_name(caller)
        if not locations:
            caller.msg(f"You cannot wear {display_name}.")
            return

        location = self._choose_location(item, display_name, locations)
        if not location:
            return

        current_location = item.db.worn_location
        if current_location == location:
            caller.msg(
                f"You are already wearing {display_name} {wear_phrase(location)}."
            )
            return

        occupying_item = caller.equipment.item_at(location)
        if occupying_item is item:
            occupying_item = None
        if occupying_item:
            occupying_name = occupying_item.get_display_name(caller)
            caller.msg(
                f"You are already wearing {occupying_name} {wear_phrase(location)}."
            )
            return

        try:
            caller.equipment.equip(item, location)
        except EquipmentError:
            # Every expected failure is reported above; this guards against a
            # concurrent or externally initiated equipment-state change.
            caller.msg(f"You cannot wear {display_name} there.")
            return
        caller.msg(f"You wear {display_name} {wear_phrase(location)}.")

    def _choose_location(
        self, item: Any, display_name: str, locations: list[str]
    ) -> str | None:
        """Resolve a requested slot or automatically choose the sole open slot."""
        caller = self.caller
        if self.location_selector in WEAR_SIDES:
            matches = [
                location
                for location in locations
                if location.startswith(f"{self.location_selector} ")
            ]
            if len(matches) == 1:
                return matches[0]
            caller.msg(
                f"You cannot wear {display_name} on your {self.location_selector} side."
            )
            return None
        if self.location_selector:
            if self.location_selector in locations:
                return self.location_selector
            caller.msg(
                f"You cannot wear {display_name} {wear_phrase(self.location_selector)}."
            )
            return None

        if len(locations) == 1:
            return locations[0]
        open_locations = [
            location
            for location in locations
            if caller.equipment.item_at(location) in (None, item)
        ]
        if len(open_locations) == 1:
            return open_locations[0]
        if not open_locations:
            caller.msg(f"You have no open location where you can wear {display_name}.")
            return None
        if all(location.split(maxsplit=1)[0] in WEAR_SIDES for location in locations):
            caller.msg(f"Wear {display_name} on which side, left or right?")
            return None

        choices = ", ".join(open_locations)
        caller.msg(f"Choose where to wear {display_name}: {choices}.")
        return None


class CmdRemove(Command):
    """
    Remove an equipped item and return it to your carried inventory.

    Usage:
      remove <item>

    The item name may be abbreviated as long as it identifies one equipped item.
    """

    key = "remove"
    locks = "cmd:all()"
    help_category = "Items"

    def func(self) -> None:
        """Find a keyword-matched equipped item and clear its worn location."""
        caller = self.caller
        item_name = self.args.strip()
        if not item_name:
            caller.msg("Remove what?")
            return

        equipped_items = [item for item in caller.contents if item.db.worn_location]
        item = caller.search(
            item_name,
            candidates=equipped_items,
            nofound_string=f"You are not wearing {item_name}.",
            multimatch_string=f"You are wearing more than one {item_name}:",
        )
        if not item:
            return

        display_name = item.get_display_name(caller)
        caller.equipment.unequip(item)
        caller.msg(f"You remove {display_name}.")

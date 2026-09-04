"""
Overrides of common Evennia game commands (look, pose, etc.).

These attach game-specific policy or presentation without reimplementing the
underlying Evennia command logic.
"""

from typing import Any

from commands.command import Command
from evennia.commands.default.general import CmdDrop as _BaseDrop
from evennia.commands.default.general import CmdGet as _BaseGet
from evennia.commands.default.general import CmdGive as _BaseGive
from evennia.commands.default.general import CmdInventory as _BaseInventory
from evennia.commands.default.general import CmdLook as _BaseLook
from evennia.commands.default.general import CmdPose as _BasePose
from evennia.utils import utils
from systems.action_policy import ActionCategory
from systems.equipment import (WEAR_LOCATIONS, WEAR_SIDES, EquipmentError,
                               allowed_wear_locations, wear_phrase)


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
        string = "|wYou are carrying:|n\n" + "\n".join(lines)
        caller.msg(text=(string, {"type": "inventory"}))


class CmdGet(_BaseGet):
    """Pick up an item when the shared action policy allows manipulation.

    Usage:
      get <item>
    """

    action_category = ActionCategory.MANIPULATE


class CmdDrop(_BaseDrop):
    """Drop a carried item when the shared action policy allows manipulation.

    Usage:
      drop <item>
    """

    action_category = ActionCategory.MANIPULATE


class CmdGive(_BaseGive):
    """Give an item when the shared action policy allows manipulation.

    Usage:
      give <item> = <character>
    """

    action_category = ActionCategory.MANIPULATE


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

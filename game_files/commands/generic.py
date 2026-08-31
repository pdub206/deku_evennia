"""
Overrides of common Evennia game commands (look, pose, etc.).

These add game-specific behaviour — currently blocking commands while
sleeping — without reimplementing the underlying logic.
"""

from commands.command import Command
from commands.position import _ASLEEP_MSG, _is_sleeping
from evennia.commands.default.general import CmdInventory as _BaseInventory
from evennia.commands.default.general import CmdLook as _BaseLook
from evennia.commands.default.general import CmdPose as _BasePose
from evennia.utils import utils


class CmdLook(_BaseLook):
    """
    Look at the room or an object.

    Usage:
      look
      look <object>
      look <direction>

    Blocked while sleeping — use |wwake|n to wake up first.
    """

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return
        super().func()


class CmdPose(_BasePose):
    """
    Pose or emote an action.

    Usage:
      pose <action>
      :<action>

    Blocked while sleeping — use |wwake|n to wake up first.
    """

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return
        super().func()


class CmdInventory(_BaseInventory):
    """
    View your inventory.

    Usage:
      inventory
      inv

    Lists what you are carrying by name only.  Use |wlook <item>|n to read an
    item's description.  Blocked while sleeping.
    """

    def func(self) -> None:
        caller = self.caller
        if _is_sleeping(caller):
            caller.msg(_ASLEEP_MSG)
            return
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

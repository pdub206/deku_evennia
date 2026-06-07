"""
Overrides of common Evennia game commands (look, pose, etc.).

These add game-specific behaviour — currently blocking commands while
sleeping — without reimplementing the underlying logic.
"""

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

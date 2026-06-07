"""
Overrides of common Evennia game commands (look, pose, etc.).

These add game-specific behaviour — currently blocking commands while
sleeping — without reimplementing the underlying logic.
"""

from commands.position import _ASLEEP_MSG, _is_sleeping
from evennia.commands.default.general import CmdLook as _BaseLook
from evennia.commands.default.general import CmdPose as _BasePose


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

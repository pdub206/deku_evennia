"""
Positional state commands: sit, rest, sleep, stand, wake.

Characters always have one of four positions:
    standing  (default)
    sitting
    resting
    sleeping

When sleeping, characters cannot look, speak, move, or use game commands.
They must type |wwake|n to return to a sitting position before acting.
"""

from evennia.commands.default.general import CmdLook as _BaseLook
from evennia.commands.default.general import CmdPose as _BasePose
from evennia.commands.default.general import CmdSay as _BaseSay

from commands.command import Command

_ASLEEP_MSG = "You are asleep and cannot do that. Type |wwake|n to wake up."

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _position(char) -> str:
    return char.db.position or "standing"


def _is_sleeping(char) -> bool:
    return _position(char) == "sleeping"


# ---------------------------------------------------------------------------
# Position commands
# ---------------------------------------------------------------------------


class CmdSit(Command):
    """
    Sit down.

    Usage:
      sit

    Your character sits down. Use |wstand|n to get back up, or |wrest|n to
    lie down and rest. You cannot sit while sleeping — use |wwake|n first.
    """

    key = "sit"
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        pos = _position(char)
        if pos == "sitting":
            char.msg("You are already sitting.")
            return
        if pos == "sleeping":
            char.msg(_ASLEEP_MSG)
            return
        char.db.position = "sitting"
        char.msg("You sit down.")
        if char.location:
            char.location.msg_contents(
                f"{char.key} sits down.", exclude=[char], from_obj=char
            )


class CmdRest(Command):
    """
    Lie down and rest.

    Usage:
      rest

    Your character lies down to rest. Use |wstand|n or |wsit|n to get up.
    You cannot rest while sleeping — use |wwake|n first.
    """

    key = "rest"
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        pos = _position(char)
        if pos == "resting":
            char.msg("You are already resting.")
            return
        if pos == "sleeping":
            char.msg(_ASLEEP_MSG)
            return
        char.db.position = "resting"
        char.msg("You lie down and rest.")
        if char.location:
            char.location.msg_contents(
                f"{char.key} lies down to rest.", exclude=[char], from_obj=char
            )


class CmdSleep(Command):
    """
    Fall asleep.

    Usage:
      sleep

    Your character falls asleep. While asleep you cannot see, speak, move,
    or take any action. Type |wwake|n to wake up.
    """

    key = "sleep"
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        if _position(char) == "sleeping":
            char.msg("You are already asleep.")
            return
        char.db.position = "sleeping"
        if char.location:
            char.location.msg_contents(
                f"{char.key} falls asleep.", exclude=[char], from_obj=char
            )
        char.msg("You fall asleep.")


class CmdStand(Command):
    """
    Stand up.

    Usage:
      stand

    Your character stands up from sitting or resting.
    You cannot stand while sleeping — use |wwake|n first.
    """

    key = "stand"
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        pos = _position(char)
        if pos == "standing":
            char.msg("You are already standing.")
            return
        if pos == "sleeping":
            char.msg(_ASLEEP_MSG)
            return
        char.db.position = "standing"
        char.msg("You stand up.")
        if char.location:
            char.location.msg_contents(
                f"{char.key} stands up.", exclude=[char], from_obj=char
            )


class CmdWake(Command):
    """
    Wake up from sleep.

    Usage:
      wake

    Wakes your character from sleep, leaving you in a sitting position.
    This is the only command available while sleeping.
    """

    key = "wake"
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        if _position(char) != "sleeping":
            char.msg("You can only wake from sleep.")
            return
        char.db.position = "sitting"
        char.msg("You wake up, finding yourself sitting.")
        if char.location:
            char.location.msg_contents(
                f"{char.key} wakes up.", exclude=[char], from_obj=char
            )


# ---------------------------------------------------------------------------
# Sleep-aware overrides for built-in Evennia commands
# ---------------------------------------------------------------------------


class CmdLook(_BaseLook):
    """Look at the room or an object. Blocked while sleeping."""

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return
        super().func()


class CmdSay(_BaseSay):
    """Say something aloud. Blocked while sleeping."""

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return
        super().func()


class CmdPose(_BasePose):
    """Pose / emote. Blocked while sleeping."""

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return
        super().func()

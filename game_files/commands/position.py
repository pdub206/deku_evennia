"""
Positional state commands: sit, rest, sleep, stand, wake.

Characters retain one of four voluntary postures:
    standing  (default)
    sitting
    resting
    sleeping

Combat, effects, and injury may impose a more restrictive effective position
without overwriting this posture. The shared action policy validates both the
command and the transition.
"""

from commands.command import Command
from systems.action_policy import ActionCategory, Position, TransitionOutcome


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
    action_category = ActionCategory.CHANGE_POSITION

    def func(self) -> None:
        char = self.caller
        result = char.actions.transition(Position.SITTING)
        if result.outcome is TransitionOutcome.DENIED:
            char.msg(result.decision.message)
            return
        if result.outcome is TransitionOutcome.ALREADY:
            char.msg("You are already sitting.")
            return
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
    action_category = ActionCategory.CHANGE_POSITION

    def func(self) -> None:
        char = self.caller
        result = char.actions.transition(Position.RESTING)
        if result.outcome is TransitionOutcome.DENIED:
            char.msg(result.decision.message)
            return
        if result.outcome is TransitionOutcome.ALREADY:
            char.msg("You are already resting.")
            return
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
    or take in-world actions. Type |wwake|n to wake up.
    """

    key = "sleep"
    help_category = "Character"
    action_category = ActionCategory.CHANGE_POSITION

    def func(self) -> None:
        char = self.caller
        result = char.actions.transition(Position.SLEEPING)
        if result.outcome is TransitionOutcome.DENIED:
            char.msg(result.decision.message)
            return
        if result.outcome is TransitionOutcome.ALREADY:
            char.msg("You are already asleep.")
            return
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
    action_category = ActionCategory.CHANGE_POSITION

    def func(self) -> None:
        char = self.caller
        result = char.actions.transition(Position.STANDING)
        if result.outcome is TransitionOutcome.DENIED:
            char.msg(result.decision.message)
            return
        if result.outcome is TransitionOutcome.ALREADY:
            char.msg("You are already standing.")
            return
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
    State-independent commands remain available while sleeping.
    """

    key = "wake"
    help_category = "Character"
    action_category = ActionCategory.WAKE

    def func(self) -> None:
        char = self.caller
        result = char.actions.transition(Position.SITTING, action=ActionCategory.WAKE)
        if result.outcome is TransitionOutcome.DENIED:
            char.msg(result.decision.message)
            return
        if result.outcome is TransitionOutcome.ALREADY:
            char.msg("You are already sitting.")
            return
        char.msg("You wake up, finding yourself sitting.")
        if char.location:
            char.location.msg_contents(
                f"{char.key} wakes up.", exclude=[char], from_obj=char
            )

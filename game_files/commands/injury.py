"""Player and staff commands supplied by COMBAT-04."""

from __future__ import annotations

from commands.command import Command, MuxCommand
from systems.action_policy import ActionCategory
from systems.combat import is_fighting, schedule_stabilization
from systems.injury import (InjuryError, InjuryState, attempt_stabilization,
                            injury_record, repair_injury)


class CmdStabilize(Command):
    """Try to stabilize a dying character with a Medicine check.

    Usage:
      stabilize <target>
      stabilise <target>
    """

    key = "stabilize"
    aliases = ("stabilise",)
    help_category = "Combat"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Validate one visible dying target and act now or queue the attempt."""
        if not self.args.strip():
            self.caller.msg("Stabilize whom?")
            return
        target = self.caller.search(self.args.strip(), location=self.caller.location)
        if target is None:
            return
        try:
            valid = (
                hasattr(target, "stats")
                and injury_record(self.caller).state is InjuryState.CONSCIOUS
                and injury_record(target).state is InjuryState.DYING
            )
        except InjuryError:
            valid = False
        if not valid:
            self.caller.msg("That character is not dying.")
            return
        if is_fighting(self.caller):
            result = schedule_stabilization(self.caller, target)
            if result.accepted:
                self.caller.msg("You prepare to stabilize them on your next action.")
            else:
                self.caller.msg("You cannot stabilize anyone right now.")
            return
        result = attempt_stabilization(self.caller, target)
        if not result.accepted:
            self.caller.msg("You cannot stabilize that character.")


class CmdInjury(MuxCommand):
    """Inspect or explicitly repair a character's injury record.

    Usage:
      @injury [<character or #dbref>]
      @injury/repair [<character or #dbref>]
    """

    key = "@injury"
    locks = "cmd:perm(Builder)"
    help_category = "Staff"
    action_category = ActionCategory.STATE_INDEPENDENT
    switch_options = ("repair",)

    def func(self) -> None:
        """Expose invalid storage without automatically changing a character."""
        target = (
            self.caller.search(self.args.strip(), global_search=True)
            if self.args.strip()
            else self.caller
        )
        if target is None:
            return
        if not hasattr(target, "stats"):
            self.caller.msg("That object has no injury state.")
            return
        if "repair" in self.switches:
            record = repair_injury(target)
            self.caller.msg(
                f"Repaired {target.key}'s injury state to {record.state.value}."
            )
            return
        try:
            record = injury_record(target)
        except InjuryError:
            self.caller.msg(
                f"{target.key}'s injury state is invalid; use @injury/repair."
            )
            return
        self.caller.msg(
            f"{target.key}: {record.state.value}; saves {record.successes} success, "
            f"{record.failures} failure; recovery token {record.last_recovery}."
        )

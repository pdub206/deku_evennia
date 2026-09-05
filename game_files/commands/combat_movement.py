"""The player-facing queued flee intent command."""

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.combat import schedule_flee
from systems.combat_movement import choose_flee_exit


class CmdFlee(Command):
    """Queue an escape attempt for your next combat action.

    Usage:
      flee [exit]
    """

    key = "flee"
    help_category = "Combat"
    action_category = ActionCategory.COMBAT

    def func(self) -> None:
        """Choose a currently eligible route and persist only one flee intent."""
        requested = None
        if self.args.strip():
            requested = self.caller.search(
                self.args.strip(), location=self.caller.location
            )
            if requested is None:
                return
        decision = choose_flee_exit(self.caller, requested)
        if not decision.allowed:
            self.caller.msg("You cannot find an escape route.")
            return
        result = schedule_flee(self.caller, decision.exit.id)
        if not result.accepted:
            self.caller.msg("You can only flee while fighting.")
            return
        if result.changed:
            self.caller.msg(
                f"You prepare to flee {decision.exit.key} on your next action."
            )
        else:
            self.caller.msg(f"You are already prepared to flee {decision.exit.key}.")

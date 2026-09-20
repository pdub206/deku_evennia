"""Read-only access to the shared world calendar."""

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.world_clock import WorldClockError, clock_presentation, clock_state


class CmdTime(Command):
    """Show the global world date, time, and daylight band. Usage: time"""

    key = "time"
    help_category = "General"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Read time without advancing it or exposing scheduler state."""
        if self.args.strip():
            self.msg("Usage: time.")
            return
        try:
            self.msg(clock_presentation(clock_state()["minute"]))
        except WorldClockError:
            self.msg("The world clock is unavailable; please notify staff.")

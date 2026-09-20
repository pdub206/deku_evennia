"""Player-facing read-only local weather command."""

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.weather import current_state


class CmdWeather(Command):
    """Report visible local weather. Usage: weather"""

    key = "weather"
    help_category = "General"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Read weather without advancing its scheduler state."""
        if self.args.strip():
            self.msg("Usage: weather.")
            return
        state = current_state(self.caller.location)
        self.msg(
            f"The weather is {state.value}."
            if state
            else "You cannot judge the weather from here."
        )

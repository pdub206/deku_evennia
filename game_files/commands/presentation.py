"""Player commands for INTERACT-05 display preferences."""

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.presentation import (
    get_presentation_profile,
    presentation_summary,
    set_presentation_preference,
)


class _TogglePreference(Command):
    """Shared query and explicit on/off handling for boolean-style settings."""

    action_category = ActionCategory.STATE_INDEPENDENT
    help_category = "General"
    profile_field = ""
    label = ""

    def current_enabled(self) -> bool:
        """Return whether this command's preference is enabled."""
        raise NotImplementedError

    def stored_value(self, enabled: bool):
        """Translate on/off into the profile field's stored value."""
        return enabled

    def func(self) -> None:
        """Query the current value or persist an explicit on/off choice."""
        value = self.args.strip().casefold()
        if not value:
            self.msg(f"{self.label} is {'on' if self.current_enabled() else 'off'}.")
            return
        if value not in {"on", "off"}:
            self.msg(f"Usage: {self.key} [on|off].")
            return
        enabled = value == "on"
        set_presentation_preference(
            self.caller, self.profile_field, self.stored_value(enabled)
        )
        self.msg(f"{self.label} {value}.")


class CmdBrief(_TogglePreference):
    """Query or toggle brief room descriptions. Usage: brief [on|off]"""

    key = "brief"
    profile_field = "room_mode"
    label = "Brief room mode"

    def current_enabled(self) -> bool:
        return get_presentation_profile(self.caller).room_mode == "brief"

    def stored_value(self, enabled: bool) -> str:
        return "brief" if enabled else "full"


class CmdCompact(_TogglePreference):
    """Query or toggle compact output spacing. Usage: compact [on|off]"""

    key = "compact"
    profile_field = "spacing"
    label = "Compact spacing"

    def current_enabled(self) -> bool:
        return get_presentation_profile(self.caller).spacing == "compact"

    def stored_value(self, enabled: bool) -> str:
        return "compact" if enabled else "normal"


class CmdAutoExits(_TogglePreference):
    """Query or toggle exits on room displays. Usage: autoexits [on|off]"""

    key = "autoexits"
    profile_field = "auto_exits"
    label = "Automatic exits"

    def current_enabled(self) -> bool:
        return get_presentation_profile(self.caller).auto_exits


class CmdPrompt(_TogglePreference):
    """Query or toggle the ordinary input prompt. Usage: prompt [on|off]"""

    key = "prompt"
    profile_field = "prompt"
    label = "Ordinary prompt"

    def current_enabled(self) -> bool:
        return get_presentation_profile(self.caller).prompt


class CmdPreferences(Command):
    """Show all display preferences. Usage: preferences"""

    key = "preferences"
    aliases = ("prefs",)
    action_category = ActionCategory.STATE_INDEPENDENT
    help_category = "General"

    def func(self) -> None:
        """Display preferences without accepting or changing arguments."""
        if self.args.strip():
            self.msg("Usage: preferences.")
            return
        self.msg(presentation_summary(self.caller))

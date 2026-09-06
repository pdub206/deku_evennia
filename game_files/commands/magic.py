"""Player commands for MAGIC-02's registry-backed magical actions."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.magic import MagicKind
from systems.magic_actions import (MagicActionError, available_actions,
                                   cast_action)
from systems.magic_resources import MagicResourceError, resource_view


class CmdCast(Command):
    """Cast one known spell or ability at an explicit target.

    Usage:
      cast <spell or ability>
      cast <spell or ability> at <target>
    """

    key = "cast"
    help_category = "Magic"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Parse the one unambiguous cast grammar and delegate all policy."""
        parsed = _parse_cast(self.args)
        if parsed is None:
            self.caller.msg("Usage: cast <spell or ability> [at <target>]")
            return
        action, target = parsed
        try:
            result = cast_action(self.caller, action, target_name=target)
        except (MagicActionError, MagicResourceError) as err:
            self.caller.msg(str(err))
            return
        if result.reason == "cast":
            self.caller.msg(f"You cast |w{result.definition.display_name}|n.")


class _MagicListCommand(Command):
    """Common read-only formatter for the spell and ability lists."""

    kind = ""
    title = ""
    action_category = ActionCategory.STATE_INDEPENDENT
    help_category = "Magic"

    def func(self) -> None:
        """List only the caller's durable, presently legal entitlements."""
        if self.args.strip():
            self.caller.msg(f"Usage: {self.key}")
            return
        try:
            actions = available_actions(self.caller, self.kind)
            resources = resource_view(self.caller)
        except MagicActionError as err:
            self.caller.msg(str(err))
            return
        lines = [f"|w{self.title}|n"]
        if actions:
            for definition in actions:
                cost = (
                    "at will"
                    if definition.cost is None
                    else f"{definition.cost.amount} {definition.cost.resource_key}"
                )
                lines.append(f"{definition.display_name}: {cost}")
        else:
            lines.append("None.")
        if resources:
            lines.append(
                "Resources: "
                + ", ".join(
                    f"{key} {current}/{maximum}" for key, current, maximum in resources
                )
            )
        self.caller.msg("\n".join(lines))


class CmdSpells(_MagicListCommand):
    """List the spells you currently know or have prepared.

    Usage:
      spells
    """

    key = "spells"
    kind = MagicKind.SPELL
    title = "Spells"


class CmdAbilities(_MagicListCommand):
    """List the class abilities you currently know or have prepared.

    Usage:
      abilities
    """

    key = "abilities"
    aliases = ("ability",)
    kind = MagicKind.ABILITY
    title = "Abilities"


def _parse_cast(raw: str) -> tuple[str, str | None] | None:
    """Split only the final `` at `` marker so multiword actions remain valid."""
    value = raw.strip()
    if not value:
        return None
    action, marker, target = value.rpartition(" at ")
    if marker:
        if not action.strip() or not target.strip():
            return None
        return action.strip(), target.strip()
    return value, None

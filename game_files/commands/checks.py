"""Builder-only, consequence-free ADV-04 diagnostic checks."""

from __future__ import annotations

from commands.command import MuxCommand
from systems.action_policy import ActionCategory
from systems.checks import CheckError, CheckRequest, resolve_check


class CmdCheck(MuxCommand):
    """Roll one visible character's diagnostic ability check without consequences.

    Usage:
      @check <target> = <ability>[/<skill>] <dc>

    This tool only shows the selected check's public mechanics. It does not
    perform an in-world action, bypass locks, or expose hidden targets.
    """

    key = "@check"
    locks = "cmd:perm(Builder)"
    help_category = "Staff"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Validate a normal visible target and emit one structured result."""
        if not self.lhs.strip() or not self.rhs.strip():
            self.caller.msg("Usage: @check <target> = <ability>[/<skill>] <dc>")
            return
        target = self.caller.search(self.lhs.strip(), location=self.caller.location)
        if target is None:
            return
        try:
            check_part, dc_text = self.rhs.rsplit(None, 1)
            ability_text, separator, skill_text = check_part.partition("/")
            check = resolve_check(
                CheckRequest(
                    target,
                    ability_text.strip(),
                    int(dc_text),
                    skill=skill_text.strip() if separator else None,
                    action_key="builder_diagnostic",
                )
            )
        except (CheckError, ValueError):
            self.caller.msg(
                "Use a known ability, optional /skill, and a DC from 5 to 30."
            )
            return
        skill = f" ({check.skill})" if check.skill else ""
        self.caller.msg(
            f"{target.key}: {check.ability}{skill} — d20 {check.die_result} + "
            f"{check.total - check.die_result} = {check.total} vs DC "
            f"{check.target_dc}: {'success' if check.success else 'failure'}."
        )

"""Player report commands backed by COMM-04A's safe durable service."""

from __future__ import annotations

from commands.command import MuxCommand
from systems.reports import submit


class CmdReport(MuxCommand):
    """Submit a durable bug, typo, or idea report.

    Usage:
      bug <text>
      typo <text>
      idea <text>
    """

    key = "bug"
    aliases = ("typo", "idea")
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Submit exactly one validated report without exposing captured context."""
        if self.switches:
            self.caller.msg("Usage: bug, typo, or idea <text>")
            return
        result = submit(self.caller, self.cmdstring.lower(), self.args)
        if result.accepted:
            self.caller.msg(f"Report #{result.report_id} submitted.")
        elif result.reason == "invalid":
            self.caller.msg("Reports need 10–2,000 characters of plain text.")
        else:
            self.caller.msg("You cannot submit a report right now.")

"""Player report commands backed by COMM-04A's safe durable service."""

from __future__ import annotations

from commands.command import MuxCommand
from systems.reports import (
    REPORT_KINDS,
    STATUSES,
    metadata,
    player_report,
    player_reports,
    purge,
    report_rows,
    submit,
    workflow,
)


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


class CmdReports(MuxCommand):
    """View only your own report status and final staff responses.

    Usage:
      reports [page]
      report/read <id>
    """

    key = "reports"
    aliases = ("report",)
    switch_options = ("read",)
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Render the intentionally redacted player-facing report surface."""
        if len(self.switches) > 1:
            self.caller.msg("Usage: reports [page] or report/read <id>")
            return
        if self.switches:
            message = player_report(self.caller, self.args.strip())
            if message is None:
                self.caller.msg("That report is unavailable.")
                return
            data = metadata(message)
            response = data.get("final_response")
            text = f"{data['kind'].title()} report — {data['status']}\nSubmitted: {data['submitted_at']}"
            if isinstance(response, str):
                text += f"\n\nStaff response:\n{response}"
            self.caller.msg(text)
            return
        try:
            page = int(self.args.strip() or "1")
        except ValueError:
            page = 0
        rows = player_reports(self.caller)
        start = (page - 1) * 20
        if page < 1 or start >= len(rows):
            self.caller.msg("You have no reports on that page.")
            return
        self.caller.msg(
            "Your reports, page %s:\n%s"
            % (
                page,
                "\n".join(
                    f"{metadata(row)['kind'].title():<5} {metadata(row)['submitted_at']}  {metadata(row)['status']}"
                    for row in rows[start : start + 20]
                ),
            )
        )


class CmdAdminReports(MuxCommand):
    """Admin-only report triage, state transitions, and retention purge.

    Usage:
      @reports [status|kind] [page]
      @reports/read <id>
      @reports/claim <id>
      @reports/note <id> = <text>
      @reports/resolve <id> = <player-safe response>
      @reports/reject <id> = <player-safe response>
      @reports/reopen <id>
      @reports/purge
    """

    key = "@reports"
    switch_options = ("read", "claim", "note", "resolve", "reject", "reopen", "purge")
    locks = "cmd:perm(Admin)"
    help_category = "Staff"
    account_caller = True

    def func(self) -> None:
        """Dispatch the bounded workflow without granting raw Message access."""
        if len(self.switches) > 1:
            self.caller.msg(
                "Usage: @reports[/read|claim|note|resolve|reject|reopen|purge]"
            )
            return
        action = self.switches[0] if self.switches else "list"
        if action == "purge":
            if self.args.strip():
                self.caller.msg("Usage: @reports/purge")
                return
            removed = purge(self.caller)
            self.caller.msg(f"Purged {removed or 0} expired report records.")
            return
        if action == "read":
            message = next(
                (
                    row
                    for row in report_rows()
                    if metadata(row).get("report_id") == self.args.strip()
                ),
                None,
            )
            if message is None:
                self.caller.msg("That report is unavailable.")
                return
            data = metadata(message)
            self.caller.msg(
                f"Report #{data['report_id']}\nKind: {data['kind']}\nStatus: {data['status']}\n"
                f"Reporter: {data.get('account_name')}\nContext: {data.get('area_key')}:{data.get('room_key')}\n\n{message.message}"
            )
            return
        if action in ("claim", "note", "resolve", "reject", "reopen"):
            report_id = self.lhs.strip() if self.lhs else self.args.strip()
            text = self.rhs if action in ("note", "resolve", "reject") else None
            result = workflow(self.caller, action, report_id, text)
            if result.accepted:
                self.caller.msg(f"Report #{result.report_id} is {result.status}.")
            else:
                self.caller.msg("That report cannot be changed.")
            return
        parts = self.args.split()
        selector = parts[0].lower() if parts else None
        try:
            page = int(parts[1] if len(parts) > 1 else "1")
        except ValueError:
            page = 0
        status = selector if selector in STATUSES else None
        kind = selector if selector in REPORT_KINDS else None
        rows = report_rows(status=status, kind=kind)
        start = (page - 1) * 20
        if page < 1 or start >= len(rows):
            self.caller.msg("No reports on that page.")
            return
        self.caller.msg(
            "Reports, page %s:\n%s"
            % (
                page,
                "\n".join(
                    f"{metadata(row)['report_id']} {metadata(row)['kind']:<5} {metadata(row)['status']}"
                    for row in rows[start : start + 20]
                ),
            )
        )

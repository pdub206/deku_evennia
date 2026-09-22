"""Bounded public-information commands backed by Evennia's live sessions."""

from __future__ import annotations

import time
from typing import Any

import evennia
from django.conf import settings
from evennia.accounts.models import AccountDB

from commands.command import MuxCommand
from systems import groups

PAGE_SIZE = 25
PUBLIC_WIZLIST_TAG = "public_wizlist"
INFORMATION_TAG_CATEGORY = "information"


def _page(raw: str) -> int | None:
    """Parse one positive page number without accepting a query language."""
    if not raw.strip():
        return 1
    try:
        page = int(raw.strip())
    except ValueError:
        return None
    return page if page > 0 else None


def _connected_accounts() -> list[tuple[Any, list[Any]]]:
    """Deduplicate Evennia's live sessions into stable Account rows."""
    by_id: dict[int, tuple[Any, list[Any]]] = {}
    for session in evennia.SESSION_HANDLER.get_sessions():
        account = getattr(session, "account", None)
        identifier = getattr(account, "id", None)
        if account is None or identifier is None:
            continue
        if identifier not in by_id:
            by_id[identifier] = (account, [])
        by_id[identifier][1].append(session)
    return sorted(by_id.values(), key=lambda item: item[0].key.casefold())


def _hidden_from_players(account: Any) -> bool:
    """Honor the explicit staff privacy tag without hiding ordinary accounts."""
    return account.check_permstring("Admin") and account.tags.has(
        "hidden", category=INFORMATION_TAG_CATEGORY
    )


def _duration(seconds: float) -> str:
    """Render a deliberately coarse duration suitable for public output."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02}m"


def _account_row(account: Any, sessions: list[Any]) -> tuple[str, str, str, str]:
    """Return only the approved public fields for one connected Account."""
    puppets = [account.get_puppet(session) for session in sessions]
    puppet = next((item for item in puppets if item is not None), None)
    name = account.key if puppet is None else f"{account.key} ({puppet.key})"
    now = time.time()
    connected = min((getattr(item, "conn_time", now) for item in sessions), default=now)
    last_command = max(
        (getattr(item, "cmd_last_visible", connected) for item in sessions),
        default=connected,
    )
    return (
        name,
        _duration(now - connected),
        _duration(now - last_command),
        "playing" if puppet else "OOC",
    )


def _paged_rows(caller: Any, rows: list[tuple[str, ...]], page: int) -> None:
    """Send one bounded page, including an unambiguous empty result."""
    start = (page - 1) * PAGE_SIZE
    subset = rows[start : start + PAGE_SIZE]
    if not subset:
        caller.msg("No matching entries on that page.")
        return
    table = caller.styled_table("|wName", "|wOn for", "|wIdle", "|wStatus")
    for row in subset:
        table.add_row(*row)
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    caller.msg(f"|wConnected accounts (page {page}/{pages}):|n\n{table}")


class CmdWho(MuxCommand):
    """List connected accounts without exposing session or network metadata.

    Usage: who [page]
           users [page]
    """

    key = "who"
    aliases = ("users",)
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Display one public row for each eligible connected Account."""
        page = _page(self.args)
        if page is None:
            self.msg("Usage: who [page]")
            return
        is_admin = self.account.check_permstring("Admin")
        rows = [
            _account_row(account, sessions)
            for account, sessions in _connected_accounts()
            if is_admin or not _hidden_from_players(account)
        ]
        _paged_rows(self, rows, page)


class CmdWhere(MuxCommand):
    """Show your own safe location and those of current group members.

    Usage: where
    """

    key = "where"
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Use GROUP-02's consented location reader, never a global query."""
        if self.args.strip():
            self.msg("Usage: where")
            return
        self.msg("|wWhere:|n\n" + "\n".join(groups.location_lines(self.caller)))


class CmdInfo(MuxCommand):
    """Show the configured public game and connection information.

    Usage: info
    """

    key = "info"
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Render only source-controlled, deployment-safe configuration."""
        if self.args.strip():
            self.msg("Usage: info")
            return
        info = settings.GAME_PUBLIC_INFO
        self.msg(
            "|w{game}|n\nRelease: {release}\nVersion: {version}\n"
            "Transports: {transports}\nRules: {rules}\nHelp: {help}\nContact: {contact}".format(
                **info
            )
        )


class CmdCredits(MuxCommand):
    """Show source-controlled game and rules attributions.

    Usage: credits
    """

    key = "credits"
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Return static attribution rather than inspecting installation paths."""
        if self.args.strip():
            self.msg("Usage: credits")
            return
        self.msg(settings.GAME_CREDITS)


class CmdWizlist(MuxCommand):
    """List staff who opted in to a public role-only roster.

    Usage: wizlist [page]
    """

    key = "wizlist"
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Show only opted-in staff names grouped by their declared public role."""
        page = _page(self.args)
        if page is None:
            self.msg("Usage: wizlist [page]")
            return
        rows = []
        for account in AccountDB.objects.all():
            if account.check_permstring("Admin") and account.tags.has(
                PUBLIC_WIZLIST_TAG, category=INFORMATION_TAG_CATEGORY
            ):
                role = account.attributes.get("public_wizlist_role", "Staff")
                if isinstance(role, str) and role.strip():
                    rows.append((role.strip(), account.key))
        rows.sort(key=lambda row: (row[0].casefold(), row[1].casefold()))
        start = (page - 1) * PAGE_SIZE
        subset = rows[start : start + PAGE_SIZE]
        if not subset:
            self.msg("No staff have opted into the public list.")
            return
        table = self.styled_table("|wRole", "|wName")
        for row in subset:
            table.add_row(*row)
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.msg(f"|wStaff list (page {page}/{pages}):|n\n{table}")


class CmdSessions(MuxCommand):
    """Show detailed live-session diagnostics to Admins only.

    Usage: sessions
    """

    key = "sessions"
    locks = "cmd:perm(Admin)"
    help_category = "Staff"
    account_caller = True

    def func(self) -> None:
        """Retain the privileged diagnostics outside the public who surface."""
        if self.args.strip():
            self.msg("Usage: sessions")
            return
        table = self.styled_table(
            "|wAccount", "|wSession", "|wProtocol", "|wHost", "|wPuppet"
        )
        for account, sessions in _connected_accounts():
            for session in sorted(sessions, key=lambda item: item.sessid):
                puppet = account.get_puppet(session)
                host = (
                    session.address[0]
                    if isinstance(session.address, tuple)
                    else session.address
                )
                table.add_row(
                    account.key,
                    str(session.sessid),
                    str(session.protocol_key),
                    str(host),
                    str(puppet or "OOC"),
                )
        self.msg(f"|wLive sessions:|n\n{table}")

"""Account-wide general board and Admin news commands."""

from __future__ import annotations

from typing import Any

from commands.command import MuxCommand
from evennia.utils import logger
from evennia.utils.eveditor import EvEditor
from systems.boards import (BOARDS, GENERAL, NEWS, ROWS_PER_PAGE, metadata,
                            pin, post, posts, read, remove, set_general_open,
                            unread_count)
from systems.mail import MAX_SUBJECT_LENGTH


def _editor_load(caller: Any) -> str:
    """Start every board post with a blank body."""
    return ""


def _editor_save(caller: Any, buffer: str) -> bool:
    """Keep the buffer until exit, when board posting is finalized."""
    return True


def _editor_quit(caller: Any) -> None:
    """Post the current editor buffer once the player exits the editor."""
    compose = getattr(caller.ndb, "board_compose", None)
    editor = getattr(caller.ndb, "_eveditor", None)
    caller.ndb.board_compose = None
    if not isinstance(compose, dict) or editor is None:
        return
    result = post(caller, compose.get("board"), compose.get("subject"), editor._buffer)
    if result.accepted:
        caller.msg(
            f"Posted #{metadata(result.message)['number']} to {compose['board']}."
        )
    elif result.reason == "closed":
        caller.msg("General board posting is currently closed.")
    elif result.reason == "invalid":
        caller.msg("Posts need a plain-text body of at most 4,000 characters.")
    else:
        caller.msg("You cannot post there.")


class CmdBoard(MuxCommand):
    """Read and post on the general board or news.

    Usage:
      board [general|news] [page]
      board/read <board> <number>
      board/post <board> <subject>
      board/remove <board> <number>
      board/remove <board> <number> = <reason>   (Admin)
      board/pin <number> = <reason>               (Admin news)
      board/unpin <number> = <reason>             (Admin news)
      board/open = <reason>                       (Admin)
      board/close = <reason>                      (Admin)

    Posting opens Evennia's text editor. Exiting it makes the editor buffer
    the permanent post body; neither subject nor body uses an equals operator.
    """

    key = "board"
    switch_options = ("read", "post", "remove", "pin", "unpin", "open", "close")
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Dispatch the intentionally fixed board surface."""
        if len(self.switches) > 1:
            self.caller.msg("Usage: board, board/read, board/post, or board/remove.")
            return
        switch = self.switches[0] if self.switches else "list"
        if switch == "list":
            self._list()
        elif switch == "read":
            self._read()
        elif switch == "post":
            self._post()
        elif switch == "remove":
            self._remove()
        elif switch in ("pin", "unpin"):
            self._pin(switch == "pin")
        else:
            self._set_open(switch == "open")

    def _list(self) -> None:
        """Display a 20-row page of one public board without ignore filtering."""
        parts = self.args.split()
        board = (
            parts.pop(0).lower() if parts and parts[0].lower() in BOARDS else GENERAL
        )
        if len(parts) > 1:
            self.caller.msg("Usage: board [general|news] [page]")
            return
        try:
            page = int(parts[0]) if parts else 1
        except ValueError:
            page = 0
        if page < 1:
            self.caller.msg("Board page numbers start at 1.")
            return
        rows = posts(board)
        page_rows = rows[(page - 1) * ROWS_PER_PAGE : page * ROWS_PER_PAGE]
        if not page_rows:
            self.caller.msg("There are no posts on that page.")
            return
        lines = [
            f"{board.title()} board ({unread_count(self.caller, board)} unread), page {page}:"
        ]
        for message in page_rows:
            facts = metadata(message)
            pin_marker = (
                "!" if message.tags.has("pinned", category="communication") else " "
            )
            lines.append(
                f"{pin_marker}{facts['number']:>5} {facts['author_name']}: {message.header}"
            )
        self.caller.msg("\n".join(lines))

    def _read(self) -> None:
        """Read a post by its stable board-local number."""
        parts = self.args.split()
        if len(parts) != 2 or parts[0].lower() not in BOARDS:
            self.caller.msg("Usage: board/read <general|news> <number>")
            return
        result = read(self.caller, parts[0].lower(), parts[1])
        if not result.accepted:
            self.caller.msg("There is no such post.")
            return
        facts = metadata(result.message)
        self.caller.msg(
            f"{facts['board'].title()} #{facts['number']} by {facts['author_name']}: {result.message.header}\n\n{result.message.message}"
        )

    def _post(self) -> None:
        """Open an editor after validating the no-equals board/subject grammar."""
        parts = self.args.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() not in BOARDS or "=" in parts[1]:
            self.caller.msg("Usage: board/post <general|news> <subject>")
            return
        board, subject = parts[0].lower(), parts[1]
        if len(subject) > MAX_SUBJECT_LENGTH:
            self.caller.msg("Board subjects may be at most 80 characters.")
            return
        if board == NEWS and not self.caller.check_permstring("Admin"):
            self.caller.msg("You cannot post there.")
            return
        self.caller.ndb.board_compose = {"board": board, "subject": subject}
        EvEditor(
            self.caller,
            loadfunc=_editor_load,
            savefunc=_editor_save,
            quitfunc=_editor_quit,
            key=f"{board} board post",
            persistent=False,
        )

    def _remove(self) -> None:
        """Remove an author's own general post or an Admin's audited target."""
        parts = self.lhs.split()
        if len(parts) != 2 or parts[0].lower() not in BOARDS:
            self.caller.msg("Usage: board/remove <general|news> <number>")
            return
        reason = (self.rhs or "").strip() or None
        result = remove(self.caller, parts[0].lower(), parts[1], reason=reason)
        if result.accepted:
            if self.caller.check_permstring("Admin"):
                logger.log_info(
                    f"COMM-03B board removal: admin=#{self.caller.id} post=#{result.message.id} reason={reason!r}"
                )
            self.caller.msg("Post removed.")
        elif result.reason == "reason_required":
            self.caller.msg("Admin removal requires a reason.")
        else:
            self.caller.msg("You cannot remove that post.")

    def _pin(self, pinned: bool) -> None:
        """Pin or unpin a news post through one Admin-only audited operation."""
        reason = (self.rhs or "").strip()
        result = pin(self.caller, self.lhs.strip(), pinned=pinned, reason=reason)
        if result.accepted:
            logger.log_info(
                f"COMM-03B news {'pin' if pinned else 'unpin'}: admin=#{self.caller.id} post=#{result.message.id} reason={reason!r}"
            )
            self.caller.msg("News post updated.")
        else:
            self.caller.msg("Usage: board/pin or board/unpin <number> = <reason>")

    def _set_open(self, open_: bool) -> None:
        """Auditably open or close general posting without limiting reading."""
        reason = (self.rhs or "").strip()
        result = set_general_open(self.caller, open_=open_, reason=reason)
        if result.accepted:
            logger.log_info(
                f"COMM-03B general {'open' if open_ else 'close'}: admin=#{self.caller.id} reason={reason!r}"
            )
            self.caller.msg(f"General posting {'opened' if open_ else 'closed'}.")
        else:
            self.caller.msg("Usage: board/open or board/close = <reason>")

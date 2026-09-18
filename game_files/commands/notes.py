"""ITEM-06 commands for reading and writing notes, plus staff inspection.

Commands only resolve names; ``systems.notes`` owns every access decision,
sanitization, and the atomic write.
"""

from __future__ import annotations

from typing import Any

from commands.command import Command, MuxCommand
from systems.action_policy import ActionCategory
from systems.notes import (
    NoteError,
    describe_authorship,
    is_note,
    note_title,
    read_denial,
    render_note,
    staff_delete_note,
    write_note,
)
from systems.visibility import room_visibility, target_visibility

_MAX_NAME_LENGTH = 128


def _readable_candidates(actor: Any) -> list[Any]:
    """Carried items plus room items the actor can currently see."""
    carried = [
        item for item in actor.contents if item.access(actor, "view", default=True)
    ]
    room = actor.location
    local = [
        obj
        for obj in (room.contents if room is not None else ())
        if obj is not actor and target_visibility(actor, obj).visible
    ]
    return carried + local


def _resolve(actor: Any, name: str, candidates: list[Any], failure: str) -> Any | None:
    """Resolve exactly one named candidate without revealing unseen objects."""
    if not name or len(name) > _MAX_NAME_LENGTH:
        actor.msg(failure)
        return None
    matches = actor.search(name, candidates=candidates, quiet=True, use_locks=False)
    if len(matches) != 1:
        actor.msg(failure)
        return None
    return matches[0]


class CmdRead(Command):
    """
    Read the writing on a note you carry or can see.

    Usage:
      read <note>

    You need enough light to see. Scrolls are not read this way; use
    |wrecite|n for those.
    """

    key = "read"
    action_category = ActionCategory.OBSERVE
    locks = "cmd:all()"
    help_category = "General"

    def func(self) -> None:
        """Resolve one visible note and show its escaped text."""
        caller = self.caller
        room = caller.location
        if room is not None and not room_visibility(caller, room).visible:
            self.msg("It is too dark to read.")
            return
        note = _resolve(
            caller,
            self.args.strip(),
            _readable_candidates(caller),
            "You do not see one thing by that name to read.",
        )
        if note is None:
            return
        denial = read_denial(caller, note)
        if denial:
            self.msg(denial)
            return
        try:
            self.msg(render_note(note, caller))
        except NoteError as err:
            self.msg(str(err))


class CmdWrite(Command):
    """
    Write on a note you are carrying, replacing what it said.

    Usage:
      write <note> = <text>

    You must carry both the note and a pen directly (not inside a bag). Use
    |w||/|n in your text to start a new line. Colour codes and other markup
    are removed. Your name is recorded as the note's last writer.
    """

    key = "write"
    action_category = ActionCategory.MANIPULATE
    locks = "cmd:all()"
    help_category = "General"

    def func(self) -> None:
        """Split on the first ``=`` and let the service validate and commit."""
        name, separator, text = self.args.partition("=")
        if not separator or not name.strip() or not text.strip():
            self.msg("Usage: write <note> = <text>")
            return
        caller = self.caller
        carried = [
            item
            for item in caller.contents
            if item.access(caller, "view", default=True)
        ]
        note = _resolve(
            caller, name.strip(), carried, "You are not carrying one note by that name."
        )
        if note is None:
            return
        try:
            write_note(caller, note, text.strip())
        except NoteError as err:
            self.msg(str(err))


_ADMIN_USAGE = "Usage: noteadmin <note> | noteadmin/delete <note> = <reason>"


class CmdNoteAdmin(MuxCommand):
    """
    Inspect a note's authorship or delete an abusive note.

    Usage:
      noteadmin <note>
      noteadmin/delete <note> = <reason>

    Inspection shows the last writer's character and account ids, their name
    when they wrote it and now, the time, revision, and the escaped body.
    Deletion requires a reason and logs the full record to the server log.
    """

    key = "noteadmin"
    locks = "cmd:perm(Builder)"
    help_category = "Builder"

    def func(self) -> None:
        """Dispatch the single inspection or audited deletion."""
        operation = next(iter(self.switches), "inspect").casefold()
        if operation not in {"inspect", "delete"} or not self.lhs:
            self.msg(_ADMIN_USAGE)
            return
        note = self.caller.search(self.lhs.strip(), global_search=True)
        if not note:
            return
        if not is_note(note):
            self.msg("That is not a note.")
            return
        if operation == "inspect":
            self.msg(
                f"|w{note.key}|n (#{note.id}) titled '{note_title(note)}' in "
                f"{getattr(note.location, 'key', 'nowhere')}\n"
                + describe_authorship(note)
            )
            return
        if not (self.rhs or "").strip():
            self.msg(_ADMIN_USAGE)
            return
        key, note_id = note.key, note.id
        try:
            staff_delete_note(note, self.caller, self.rhs)
        except NoteError as err:
            self.msg(str(err))
            return
        self.msg(f"Deleted {key} (#{note_id}).")

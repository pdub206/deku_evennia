"""ITEM-06 readable and writable notes.

A note is an ordinary ``type = note`` Item. Its builder-authored title lives in
``note_title``; the player-written body and its authorship share one versioned
``note_record`` Attribute, so a write replaces both in a single row update and a
failed or concurrent write leaves the previous complete record in place.

Player text is stored as sanitized plain text: Evennia colour/MXP markup is
stripped, every remaining ``|`` is replaced so no markup can be reassembled,
and control/format characters are removed. :func:`render_note` escapes the
stored text again for display, so telnet and the webclient show it literally.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia.utils import logger
from evennia.utils.ansi import raw, strip_ansi, strip_mxp
from systems.action_policy import ActionCategory
from systems.item_resources import ItemResourceError, item_mutation

NOTE_TYPE = "note"
PEN_TYPE = "pen"
TITLE_ATTRIBUTE = "note_title"
RECORD_ATTRIBUTE = "note_record"
RECORD_VERSION = 1
DEFAULT_TITLE_MAX_LENGTH = 80
DEFAULT_BODY_MAX_LENGTH = 2000
MAX_DISPLAY_NAME_LENGTH = 80
MAX_REASON_LENGTH = 200
# Oversized raw input is refused before any normalization work is spent on it.
RAW_INPUT_FACTOR = 4
PIPE_REPLACEMENT = "\u00a6"
# Joiners are format characters that ordinary scripts and emoji rely on.
_KEPT_FORMAT_CHARACTERS = frozenset({"\u200c", "\u200d"})
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_LINE_SEPARATORS = ("\r\n", "\r", "\u2028", "\u2029", "\x85")
_RECORD_KEYS = frozenset(
    {
        "version",
        "revision",
        "body",
        "character_id",
        "account_id",
        "display_name",
        "written_at",
    }
)


class NoteError(ValueError):
    """A player-safe denial, or a malformed note needing staff repair."""


def title_max_length() -> int:
    """Settings-managed maximum title length, in characters."""
    return int(getattr(settings, "NOTE_TITLE_MAX_LENGTH", DEFAULT_TITLE_MAX_LENGTH))


def body_max_length() -> int:
    """Settings-managed maximum body length, in characters."""
    return int(getattr(settings, "NOTE_BODY_MAX_LENGTH", DEFAULT_BODY_MAX_LENGTH))


def is_note(obj: Any) -> bool:
    """Match an ordinary Item authored as a note."""
    return bool(
        obj is not None
        and obj.is_typeclass("typeclasses.objects.Item")
        and obj.attributes.get("type") == NOTE_TYPE
    )


def is_pen(obj: Any) -> bool:
    """Match an ordinary Item authored as a pen."""
    return bool(
        obj is not None
        and obj.is_typeclass("typeclasses.objects.Item")
        and obj.attributes.get("type") == PEN_TYPE
    )


def sanitize_text(raw_text: Any, *, multiline: bool, max_length: int) -> str:
    """Normalize untrusted text to bounded, markup-free plain text.

    Markup is stripped (``|/`` becomes a line break), Unicode is NFC-normalized,
    line endings and tabs are unified, control/format characters are removed,
    trailing spaces and repeated blank lines collapse, and any pipe left over is
    replaced so the stored text can never form Evennia markup. The limit is
    checked after normalization and oversized text is refused, not truncated.
    """
    if not isinstance(raw_text, str):
        raise NoteError("That text cannot be written.")
    if len(raw_text) > max_length * RAW_INPUT_FACTOR:
        raise NoteError(f"That is too long; the limit is {max_length} characters.")
    # The ANSI parser un-doubles "{{", so pre-double braces to keep them literal.
    text = strip_ansi(strip_mxp(raw_text.replace("{", "{{")))
    text = unicodedata.normalize("NFC", text)
    for separator in _LINE_SEPARATORS:
        text = text.replace(separator, "\n")
    text = text.replace("\t", " ").replace("|", PIPE_REPLACEMENT)
    text = "".join(
        char
        for char in text
        if char == "\n"
        or char in _KEPT_FORMAT_CHARACTERS
        or unicodedata.category(char) not in _REMOVED_CATEGORIES
    )
    lines = [line.rstrip() for line in text.split("\n")]
    if multiline:
        kept: list[str] = []
        for line in lines:
            if line or (kept and kept[-1]):
                kept.append(line)
        text = "\n".join(kept).strip("\n")
    else:
        text = " ".join(line.strip() for line in lines if line.strip())
    if len(text) > max_length:
        raise NoteError(f"That is too long; the limit is {max_length} characters.")
    return text


def sanitize_title(raw_title: Any) -> str:
    """Validate one builder-authored single-line note title."""
    title = sanitize_text(raw_title, multiline=False, max_length=title_max_length())
    if not title:
        raise NoteError("A note title needs some text.")
    return title


def note_record(note: Any) -> dict[str, Any] | None:
    """Return the validated authorship/body record, or None for a blank note."""
    raw_record = note.attributes.get(RECORD_ATTRIBUTE)
    if raw_record is None:
        return None
    if (
        not isinstance(raw_record, Mapping)
        or set(raw_record) != _RECORD_KEYS
        or raw_record["version"] != RECORD_VERSION
    ):
        raise NoteError("That note needs staff repair.")
    for key in ("version", "revision", "character_id"):
        value = raw_record[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise NoteError("That note needs staff repair.")
    account_id = raw_record["account_id"]
    if account_id is not None and (
        isinstance(account_id, bool) or not isinstance(account_id, int)
    ):
        raise NoteError("That note needs staff repair.")
    for key in ("body", "display_name", "written_at"):
        if not isinstance(raw_record[key], str):
            raise NoteError("That note needs staff repair.")
    return dict(raw_record)


def note_title(note: Any) -> str:
    """Return the authored title, falling back to the item's own name."""
    title = note.attributes.get(TITLE_ATTRIBUTE)
    return title if isinstance(title, str) and title else note.key


def _escape(text: str) -> str:
    """Escape stored plain text for every outgoing Evennia text pipeline."""
    escaped = raw(text)
    if getattr(settings, "FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED", False):
        escaped = escaped.replace("$", "\\$")
    return escaped


def render_note(note: Any, reader: Any) -> str:
    """Return the player-facing reading of a note with all untrusted text escaped."""
    record = note_record(note)
    lines = [f"|w{_escape(note_title(note))}|n"]
    if record is None or not record["body"]:
        lines.append("It is blank.")
        return "\n".join(lines)
    lines.append(_escape(record["body"]))
    lines.append(f"(Last written by {_escape(record['display_name'])}.)")
    return "\n".join(lines)


def _carried(actor: Any, item: Any) -> bool:
    """Directly carried and still perceivable/usable by the actor."""
    return bool(
        item.location is actor
        and item.access(actor, "view", default=True)
        and item.access(actor, "interact", default=True)
    )


def carried_pen(actor: Any) -> Any | None:
    """Return one directly carried usable pen; pens inside containers do not count."""
    return next(
        (item for item in actor.contents if is_pen(item) and _carried(actor, item)),
        None,
    )


def read_denial(reader: Any, note: Any) -> str | None:
    """Return a player-safe reason the reader cannot read this note, or None."""
    decision = reader.actions.check(ActionCategory.OBSERVE)
    if not decision.allowed:
        return decision.message
    if not is_note(note):
        return "There is nothing written on that to read."
    if not note.access(reader, "read", default=True):
        return "You cannot read that."
    return None


def _write_denial(writer: Any, note: Any) -> str | None:
    """Revalidate ownership, pen, write lock, and action policy."""
    decision = writer.actions.check(ActionCategory.MANIPULATE)
    if not decision.allowed:
        return decision.message
    if not is_note(note):
        return "You can only write on a note."
    if writer.location is None or not _carried(writer, note):
        return "You must be carrying the note to write on it."
    if not note.access(writer, "write", default=True):
        return "You cannot write on that."
    if carried_pen(writer) is None:
        return "You need to be carrying a pen to write."
    return None


def _store(note: Any, record: dict[str, Any]) -> None:
    """Replace the whole record in one Attribute write."""
    note.attributes.add(RECORD_ATTRIBUTE, record)


def write_note(writer: Any, note: Any, raw_text: Any) -> dict[str, Any]:
    """Atomically replace a note's body and authorship, returning the new record.

    Every check runs again inside the serialized transaction immediately before
    the write, so a note or pen that moved, a lock that changed, or a position
    change between parsing and commit refuses the write. Any failure rolls the
    transaction back and leaves the previous complete record.
    """
    try:
        with item_mutation(writer, note):
            denial = _write_denial(writer, note)
            if denial:
                raise NoteError(denial)
            body = sanitize_text(raw_text, multiline=True, max_length=body_max_length())
            if not body:
                raise NoteError("Write what?")
            previous = note_record(note)
            account = getattr(writer, "account", None)
            record = {
                "version": RECORD_VERSION,
                "revision": (previous["revision"] if previous else 0) + 1,
                "body": body,
                "character_id": writer.id,
                "account_id": getattr(account, "id", None),
                "display_name": sanitize_text(
                    writer.key, multiline=False, max_length=MAX_DISPLAY_NAME_LENGTH
                )
                or f"#{writer.id}",
                "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            _store(note, record)
            _announce(writer, note)
            return record
    except ItemResourceError as err:
        raise NoteError("Someone else is using that note right now.") from err


def _announce(writer: Any, note: Any) -> None:
    """Send one actor and one room message only after the write commits."""
    room = writer.location

    def deliver() -> None:
        try:
            writer.msg(f"You write on {note.get_display_name(writer)}.")
            room.msg_contents(
                "{writer} writes something on {note}.",
                mapping={"writer": writer, "note": note},
                exclude=writer,
            )
        except Exception:
            logger.log_err("Note write message delivery failed after commit.")

    transaction.on_commit(deliver)


def describe_authorship(note: Any) -> str:
    """Staff view of the raw record, including the writer's current identity."""
    try:
        record = note_record(note)
    except NoteError:
        return f"  record: {note.attributes.get(RECORD_ATTRIBUTE)!r} |r(malformed)|n"
    if record is None:
        return "  record: blank"
    from evennia.objects.models import ObjectDB

    current = ObjectDB.objects.filter(pk=record["character_id"]).first()
    current_name = current.key if current is not None else "deleted"
    return "\n".join(
        (
            f"  revision {record['revision']} written {record['written_at']}",
            f"  writer: #{record['character_id']} (now {_escape(current_name)}), "
            f"account #{record['account_id']}",
            f"  name when written: {_escape(record['display_name'])}",
            f"  body ({len(record['body'])} characters):",
            _escape(record["body"]),
        )
    )


def staff_delete_note(note: Any, actor: Any, reason: Any) -> None:
    """Delete an abusive note, logging its authorship and the staff reason."""
    if not isinstance(reason, str) or not reason.strip():
        raise NoteError("Deleting a note needs a reason.")
    if not is_note(note):
        raise NoteError("That is not a note.")
    if note.contents:
        raise NoteError("That note holds other objects; move them first.")
    try:
        record = note_record(note)
    except NoteError:
        record = note.attributes.get(RECORD_ATTRIBUTE)
    note_id = note.id
    logger.log_info(
        f"Note deleted: #{note_id} by #{actor.id} "
        f"({reason.strip()[:MAX_REASON_LENGTH]}); record {record!r}"
    )
    if not note.delete():
        raise NoteError("That note could not be deleted.")

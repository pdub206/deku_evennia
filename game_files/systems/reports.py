"""Safe, durable player-report capture for COMM-04A.

Reports use locked Evennia Messages solely as durable records.  The metadata
tag contains only primitive identity and location snapshots, so it remains
useful after an Account, character, or room is renamed or deleted.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from evennia.accounts.models import AccountDB
from evennia.comms.models import Msg
from systems.areas import area_of, room_key_of

REPORT_TAG = "deku_report"
REPORT_CATEGORY = "communication"
UNRESOLVED_TAG = "unresolved"
REPORT_VERSION = 1
REPORT_KINDS = ("bug", "typo", "idea")
MIN_BODY_LENGTH = 10
MAX_BODY_LENGTH = 2000
ROLLING_WINDOW = timedelta(minutes=10)
MAX_WINDOW_REPORTS = 3
MAX_UNRESOLVED_REPORTS = 20
MAX_STAFF_NOTES = 50
MAX_NOTE_LENGTH = 2000
MAX_RESPONSE_LENGTH = 2000
NOTICE_ATTRIBUTE = "pending_report_notices"
MAX_PENDING_NOTICES = 100
FINAL_STATUSES = ("resolved", "rejected")
STATUSES = ("submitted", "claimed", *FINAL_STATUSES)
RETENTION = timedelta(days=180)


@dataclass(frozen=True)
class ReportResult:
    """A report-operation outcome that never exposes operational details."""

    accepted: bool
    reason: str
    report_id: str | None = None
    status: str | None = None
    message: Any | None = None


def normalize_body(raw: Any) -> str | None:
    """Normalize one bounded plain-text report to a logical line."""
    if not isinstance(raw, str):
        return None
    body = unicodedata.normalize("NFC", raw)
    if "|" in body or any(unicodedata.category(char).startswith("C") for char in body):
        return None
    body = " ".join(body.split())
    if not MIN_BODY_LENGTH <= len(body) <= MAX_BODY_LENGTH:
        return None
    return body


def report_messages():
    """Return only records owned by this feature's exact tag."""
    return Msg.objects.filter(
        db_tags__db_key=REPORT_TAG, db_tags__db_category=REPORT_CATEGORY
    )


def _safe_display(raw: Any) -> str | None:
    """Keep a bounded display fallback without persisting client markup."""
    if not isinstance(raw, str):
        return None
    value = unicodedata.normalize("NFC", raw)
    value = "".join(
        char
        for char in value
        if char != "|" and not unicodedata.category(char).startswith("C")
    )
    value = " ".join(value.split())[:120]
    return value or None


def _puppet(account: Any) -> Any | None:
    """Find the account's current puppet without falling back to stored characters."""
    try:
        puppets = [
            session.puppet for session in account.sessions.all() if session.puppet
        ]
    except (AttributeError, TypeError):
        return None
    return puppets[0] if puppets else None


def _context(account: Any) -> dict[str, Any]:
    """Snapshot only the reporter and current room's safe, stable identifiers."""
    character = _puppet(account)
    room = getattr(character, "location", None)
    display = _safe_display(getattr(room, "key", None))
    return {
        "account_id": account.id,
        "account_name": _safe_display(getattr(account, "key", None)),
        "character_id": getattr(character, "id", None),
        "character_name": _safe_display(getattr(character, "key", None)),
        "area_key": area_of(room) if room is not None else None,
        "room_key": room_key_of(room) if room is not None else None,
        "room_name": display,
        "room_dbref": f"#{room.id}" if getattr(room, "id", None) else None,
    }


def metadata(message: Any) -> dict[str, Any]:
    """Return the immutable primitive snapshot for a valid report record."""
    tag = message.tags.get(
        f"report-{message.id}", category=REPORT_CATEGORY, return_tagobj=True
    )
    try:
        return json.loads(tag.db_data) if tag and tag.db_data else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _metadata_tag(message: Any) -> Any | None:
    """Return this report's one mutable metadata tag, if structurally present."""
    return message.tags.get(
        f"report-{message.id}", category=REPORT_CATEGORY, return_tagobj=True
    )


def _save_metadata(message: Any, data: dict[str, Any]) -> bool:
    """Persist only JSON-compatible report metadata through its unique tag."""
    tag = _metadata_tag(message)
    if tag is None:
        return False
    tag.db_data = json.dumps(data, sort_keys=True)
    tag.save(update_fields=["db_data"])
    return True


def _report_for_id(report_id: Any, *, lock: bool = False) -> Any | None:
    """Find a report by opaque public id, ignoring malformed tag data."""
    if not isinstance(report_id, str) or len(report_id) != 32:
        return None
    rows = report_messages()
    if lock:
        rows = rows.select_for_update()
    for message in rows:
        if metadata(message).get("report_id") == report_id:
            return message
    return None


def _plain_text(raw: Any, maximum: int) -> str | None:
    """Normalize a staff-authored player-safe line without formatter markup."""
    if not isinstance(raw, str):
        return None
    value = unicodedata.normalize("NFC", raw)
    if "|" in value or any(
        unicodedata.category(char).startswith("C") for char in value
    ):
        return None
    value = " ".join(value.split())
    return value if value and len(value) <= maximum else None


def _actor_snapshot(actor: Any) -> dict[str, Any]:
    """Record a bounded staff identity snapshot without retaining an Account object."""
    return {"id": actor.id, "name": _safe_display(actor.key)}


def _notice_entries(account: Any) -> list[dict[str, str]] | None:
    """Read valid, bounded pending notices; malformed state is repaired empty."""
    raw = getattr(account.db, NOTICE_ATTRIBUTE, None)
    if raw is None:
        return []
    if not isinstance(raw, list):
        account.attributes.remove(NOTICE_ATTRIBUTE)
        return []
    valid = [
        entry
        for entry in raw
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and isinstance(entry.get("message"), str)
    ]
    if len(valid) != len(raw):
        setattr(account.db, NOTICE_ATTRIBUTE, valid)
    return valid


def deliver_report_notices(account: Any) -> int:
    """Remove queued notices before their one account-level delivery attempt."""
    entries = _notice_entries(account) or []
    setattr(account.db, NOTICE_ATTRIBUTE, [])
    for entry in entries:
        account.msg(entry["message"])
    return len(entries)


def _queue_status_notice(account: Any, report_id: str, status: str) -> bool:
    """Deliver online once or persist one offline notice, respecting its hard cap."""
    text = f"Report #{report_id} status: {status}."
    if account.sessions.count():
        transaction.on_commit(lambda: account.msg(text))
        return True
    entries = _notice_entries(account) or []
    notice_id = f"report-status:{report_id}:{status}"
    if any(entry["id"] == notice_id for entry in entries):
        return True
    if len(entries) >= MAX_PENDING_NOTICES:
        return False
    entries.append({"id": notice_id, "message": text})
    setattr(account.db, NOTICE_ATTRIBUTE, entries)
    return True


def report_rows(*, status: str | None = None, kind: str | None = None) -> list[Any]:
    """Return valid reports newest first, optionally narrowed by public fields."""
    rows = []
    for message in report_messages():
        data = metadata(message)
        if (
            data.get("report_id")
            and (status is None or data.get("status") == status)
            and (kind is None or data.get("kind") == kind)
        ):
            rows.append(message)
    return sorted(rows, key=lambda message: message.db_date_created, reverse=True)


def player_reports(account: Any) -> list[Any]:
    """Return only this Account's reports, never staff-wide Message rows."""
    return [
        message
        for message in report_rows()
        if metadata(message).get("account_id") == account.id
    ]


def player_report(account: Any, report_id: Any) -> Any | None:
    """Resolve one reporter-owned record without revealing other report ids."""
    message = _report_for_id(report_id)
    return (
        message
        if message and metadata(message).get("account_id") == account.id
        else None
    )


def _audit(data: dict[str, Any], actor: Any, event: str) -> None:
    """Append one bounded primitive staff audit entry to a report snapshot."""
    audits = data.get("audits") if isinstance(data.get("audits"), list) else []
    audits.append(
        {
            "at": timezone.now().isoformat(),
            "actor": _actor_snapshot(actor),
            "event": event,
        }
    )
    data["audits"] = audits[-100:]


def workflow(actor: Any, action: str, report_id: Any, text: Any = None) -> ReportResult:
    """Apply one Admin-only claim/note/status transition under durable locks."""
    if action not in ("claim", "note", "resolve", "reject", "reopen") or not getattr(
        actor, "check_permstring", lambda _: False
    )("Admin"):
        return ReportResult(False, "denied")
    if action == "note":
        text = _plain_text(text, MAX_NOTE_LENGTH)
    elif action in ("resolve", "reject"):
        text = _plain_text(text, MAX_RESPONSE_LENGTH)
    if action in ("note", "resolve", "reject") and text is None:
        return ReportResult(False, "invalid_text")
    with transaction.atomic():
        message = _report_for_id(report_id, lock=True)
        if message is None:
            return ReportResult(False, "missing")
        data = metadata(message)
        status = data.get("status")
        if status not in STATUSES:
            return ReportResult(False, "malformed")
        now = timezone.now().isoformat()
        if action == "note":
            notes = data.get("notes") if isinstance(data.get("notes"), list) else []
            if len(notes) >= MAX_STAFF_NOTES:
                return ReportResult(False, "note_limit")
            notes.append({"at": now, "actor": _actor_snapshot(actor), "text": text})
            data["notes"] = notes
            _audit(data, actor, "note")
        elif action == "claim":
            if status == "submitted":
                data["status"] = "claimed"
                data["claimant"] = {**_actor_snapshot(actor), "at": now}
                _audit(data, actor, "claim")
                reporter = (
                    AccountDB.objects.select_for_update()
                    .filter(pk=data.get("account_id"))
                    .first()
                )
                if reporter is None:
                    data["missing_recipient_audit"] = {"at": now, "event": "claim"}
                elif not _queue_status_notice(reporter, data["report_id"], "claimed"):
                    return ReportResult(False, "notice_limit")
            elif status != "claimed":
                return ReportResult(False, "invalid_transition")
        elif action in ("resolve", "reject"):
            final_status = "resolved" if action == "resolve" else "rejected"
            if status in FINAL_STATUSES and status != final_status:
                return ReportResult(False, "invalid_transition")
            if status not in FINAL_STATUSES:
                data.update(status=final_status, final_response=text, final_at=now)
                message.tags.remove(UNRESOLVED_TAG, category=REPORT_CATEGORY)
                _audit(data, actor, final_status)
                reporter = (
                    AccountDB.objects.select_for_update()
                    .filter(pk=data.get("account_id"))
                    .first()
                )
                if reporter is None:
                    data["missing_recipient_audit"] = {
                        "at": now,
                        "event": final_status,
                    }
                elif not _queue_status_notice(
                    reporter, data["report_id"], final_status
                ):
                    return ReportResult(False, "notice_limit")
        else:  # reopen
            if status in FINAL_STATUSES:
                data.pop("final_at", None)
                data.pop("final_response", None)
                data["status"] = "submitted"
                message.tags.add(UNRESOLVED_TAG, category=REPORT_CATEGORY)
                _audit(data, actor, "reopen")
                reporter = (
                    AccountDB.objects.select_for_update()
                    .filter(pk=data.get("account_id"))
                    .first()
                )
                if reporter is None:
                    data["missing_recipient_audit"] = {"at": now, "event": "reopen"}
                elif not _queue_status_notice(reporter, data["report_id"], "submitted"):
                    return ReportResult(False, "notice_limit")
        if not _save_metadata(message, data):
            return ReportResult(False, "malformed")
    return ReportResult(True, "ok", data["report_id"], data["status"], message)


def purge(actor: Any) -> int | None:
    """Explicitly delete only final report records whose 180-day audit window ended."""
    if not getattr(actor, "check_permstring", lambda _: False)("Admin"):
        return None
    cutoff = timezone.now() - RETENTION
    removed = 0
    for message in report_rows():
        data = metadata(message)
        if data.get("status") not in FINAL_STATUSES:
            continue
        try:
            final_at = datetime.fromisoformat(data["final_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if final_at <= cutoff:
            message.delete()
            removed += 1
    return removed


def submit(account: Any, kind: Any, raw_body: Any) -> ReportResult:
    """Atomically validate limits and persist one report with safe snapshots."""
    body = normalize_body(raw_body)
    if account is None or kind not in REPORT_KINDS or body is None:
        return ReportResult(False, "invalid")
    try:
        with transaction.atomic():
            locked = (
                type(account).objects.select_for_update().filter(pk=account.pk).first()
            )
            if locked is None:
                return ReportResult(False, "unavailable")
            now = timezone.now()
            authored = report_messages().filter(db_sender_accounts=locked)
            if (
                authored.filter(db_date_created__gte=now - ROLLING_WINDOW).count()
                >= MAX_WINDOW_REPORTS
            ):
                return ReportResult(False, "rate_limited")
            if (
                authored.filter(
                    db_tags__db_key=UNRESOLVED_TAG, db_tags__db_category=REPORT_CATEGORY
                ).count()
                >= MAX_UNRESOLVED_REPORTS
            ):
                return ReportResult(False, "unresolved_limit")
            message = Msg.objects.create_message(
                locked,
                body,
                locks="read:perm(Admin);delete:perm(Admin);edit:perm(Admin)",
                tags=((REPORT_TAG, REPORT_CATEGORY), (UNRESOLVED_TAG, REPORT_CATEGORY)),
            )
            # A random opaque id avoids exposing Message-row sequencing while
            # remaining stable and collision-safe across concurrent submissions.
            report_id = uuid4().hex
            snapshot = {
                "version": REPORT_VERSION,
                "report_id": report_id,
                "kind": kind,
                "status": "submitted",
                "submitted_at": now.isoformat(),
                "build_id": _safe_display(
                    getattr(settings, "GAME_BUILD_ID", "development")
                ),
                **_context(locked),
            }
            message.tags.add(
                f"report-{message.id}",
                category=REPORT_CATEGORY,
                data=json.dumps(snapshot, sort_keys=True),
            )
    except Exception:
        # Persistence failures disclose no database or deployment details; the
        # surrounding atomic block rolls back every partial feature write.
        return ReportResult(False, "storage_failed")
    return ReportResult(True, "ok", report_id, "submitted", message)

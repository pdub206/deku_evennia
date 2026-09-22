"""Safe, durable player-report capture for COMM-04A.

Reports use locked Evennia Messages solely as durable records.  The metadata
tag contains only primitive identity and location snapshots, so it remains
useful after an Account, character, or room is renamed or deleted.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
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

"""Durable, account-scoped mail backed by tagged and locked Evennia Messages."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from evennia.comms.models import Msg
from systems.communication import ignored_by

MAIL_TAG = "deku_mail"
MAIL_CATEGORY = "communication"
UNREAD_TAG = "unread"
MAX_SUBJECT_LENGTH = 80
MAX_BODY_LENGTH = 4000
MAX_LIVE_MESSAGES = 200
RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class MailResult:
    """The result of a mail operation without exposing target details."""

    accepted: bool
    reason: str
    message: Any | None = None


def normalize_subject(raw: Any) -> str | None:
    """Accept one bounded plain-text subject, rejecting client control markup."""
    if not isinstance(raw, str):
        return None
    subject = unicodedata.normalize("NFC", raw)
    if "|" in subject or any(
        unicodedata.category(char).startswith("C") for char in subject
    ):
        return None
    subject = " ".join(subject.split())
    return subject if subject and len(subject) <= MAX_SUBJECT_LENGTH else None


def normalize_body(raw: Any) -> str | None:
    """Accept an immutable editor body while retaining intentional line breaks."""
    if not isinstance(raw, str):
        return None
    body = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    if "|" in body or any(
        unicodedata.category(char).startswith("C") and char != "\n" for char in body
    ):
        return None
    body = "\n".join(line.rstrip() for line in body.split("\n")).strip()
    return body if body and len(body) <= MAX_BODY_LENGTH else None


def mail_messages():
    """Return only this feature's Messages, never generic tells or channels."""
    return Msg.objects.filter(
        db_tags__db_key=MAIL_TAG, db_tags__db_category=MAIL_CATEGORY
    )


def received(account: Any):
    """Return the recipient's non-deleted mail newest first."""
    return (
        mail_messages()
        .filter(db_receivers_accounts=account)
        .exclude(db_hide_from_accounts=account)
        .order_by("-db_date_created", "-id")
    )


def sent(account: Any):
    """Return the sender's non-deleted mail newest first."""
    return (
        mail_messages()
        .filter(db_sender_accounts=account)
        .exclude(db_hide_from_accounts=account)
        .order_by("-db_date_created", "-id")
    )


def unread_count(account: Any) -> int:
    """Count receiver-visible unread messages without trusting caller state."""
    return (
        received(account)
        .filter(db_tags__db_key=UNREAD_TAG, db_tags__db_category=MAIL_CATEGORY)
        .count()
    )


def _metadata(message: Any) -> tuple[Any | None, dict[str, Any]]:
    """Fetch this Message's unique snapshot tag without accepting malformed data."""
    tag = message.tags.get(
        f"mail-{message.id}", category=MAIL_CATEGORY, return_tagobj=True
    )
    try:
        return tag, json.loads(tag.db_data) if tag and tag.db_data else {}
    except (TypeError, json.JSONDecodeError):
        return tag, {}


def snapshots(message: Any) -> dict[str, Any]:
    """Return the saved account identity snapshots used for safe mail display."""
    return _metadata(message)[1]


def send(sender: Any, recipient: Any, subject_raw: Any, body_raw: Any) -> MailResult:
    """Atomically revalidate access, ignore, quotas, and create one locked Msg."""
    subject, body = normalize_subject(subject_raw), normalize_body(body_raw)
    if subject is None or body is None:
        return MailResult(False, "invalid_text")
    if recipient is None or recipient == sender:
        return MailResult(False, "unavailable")
    with transaction.atomic():
        # Lock both account rows in id order so competing sends see the quota.
        accounts = list(
            type(sender)
            .objects.select_for_update()
            .filter(pk__in=(sender.pk, recipient.pk))
            .order_by("pk")
        )
        locked = {account.pk: account for account in accounts}
        sender, recipient = locked.get(sender.pk), locked.get(recipient.pk)
        if (
            sender is None
            or recipient is None
            or not recipient.access(sender, "msg")
            or ignored_by(recipient, sender)
        ):
            return MailResult(False, "unavailable")
        if (
            received(recipient).count() >= MAX_LIVE_MESSAGES
            or sent(sender).count() >= MAX_LIVE_MESSAGES
        ):
            return MailResult(False, "unavailable")
        message = Msg.objects.create_message(
            sender,
            body,
            receivers=recipient,
            header=subject,
            locks=f"read:id({sender.id}) or id({recipient.id}) or perm(Admin);delete:id({sender.id}) or id({recipient.id}) or perm(Admin)",
            tags=((MAIL_TAG, MAIL_CATEGORY), (UNREAD_TAG, MAIL_CATEGORY)),
        )
        # A unique tag is deliberate: Tags normally describe shared state, but
        # this one stores immutable account id/name snapshots for this Msg alone.
        message.tags.add(
            f"mail-{message.id}",
            category=MAIL_CATEGORY,
            data=json.dumps(
                {
                    "sender_id": sender.id,
                    "sender_name": sender.key,
                    "recipient_id": recipient.id,
                    "recipient_name": recipient.key,
                    "deleted_at": None,
                },
                sort_keys=True,
            ),
        )
    return MailResult(True, "ok", message)


def read(account: Any, message_id: Any) -> MailResult:
    """Open one recipient-visible message and clear only its unread marker."""
    try:
        message = received(account).get(pk=int(message_id))
    except (Msg.DoesNotExist, TypeError, ValueError):
        return MailResult(False, "missing")
    if not message.access(account, "read"):
        return MailResult(False, "missing")
    message.tags.remove(UNREAD_TAG, category=MAIL_CATEGORY)
    return MailResult(True, "ok", message)


def reply_target(account: Any, message_id: Any) -> Any | None:
    """Resolve the sole original sender only from a visible received message."""
    result = read(account, message_id)
    if not result.accepted or len(result.message.senders) != 1:
        return None
    return result.message.senders[0]


def delete(account: Any, message_id: Any) -> MailResult:
    """Hide a message from one party and delete it once both views are gone."""
    try:
        message = mail_messages().get(pk=int(message_id))
    except (Msg.DoesNotExist, TypeError, ValueError):
        return MailResult(False, "missing")
    if account not in [*message.senders, *message.receivers] or not message.access(
        account, "delete"
    ):
        return MailResult(False, "missing")
    message.db_hide_from_accounts.add(account)
    parties = [*message.senders, *message.receivers]
    if parties and all(
        message.db_hide_from_accounts.filter(pk=party.pk).exists() for party in parties
    ):
        tag, metadata = _metadata(message)
        if tag is not None:
            metadata["deleted_at"] = timezone.now().isoformat()
            tag.db_data = json.dumps(metadata, sort_keys=True)
            tag.save(update_fields=["db_data"])
    return MailResult(True, "ok")


def purge_expired() -> int:
    """Purge mail 30 days after its last remaining view was deleted."""
    cutoff = timezone.now() - RETENTION
    candidates = mail_messages()
    removed = 0
    for message in candidates:
        parties = [*message.senders, *message.receivers]
        tag, metadata = _metadata(message)
        deleted_at = metadata.get("deleted_at")
        if not isinstance(deleted_at, str):
            continue
        try:
            final_deleted = datetime.fromisoformat(deleted_at)
        except ValueError:
            continue
        if (
            final_deleted <= cutoff
            and parties
            and all(
                message.db_hide_from_accounts.filter(pk=party.pk).exists()
                for party in parties
            )
        ):
            message.delete()
            removed += 1
    return removed

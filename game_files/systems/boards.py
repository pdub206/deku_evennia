"""Message-backed general and news boards for COMM-03B."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from evennia.comms.models import Msg
from evennia.server.models import ServerConfig
from systems.mail import normalize_body, normalize_subject

BOARD_TAG = "deku_board"
BOARD_CATEGORY = "communication"
REMOVED_TAG = "removed"
PINNED_TAG = "pinned"
GENERAL = "general"
NEWS = "news"
BOARDS = (GENERAL, NEWS)
ROWS_PER_PAGE = 20
GENERAL_VISIBLE_LIMIT = 200
GENERAL_RETENTION = timedelta(days=180)
REMOVED_RETENTION = timedelta(days=30)
STATE_KEY = "comm_03b_board_state"


@dataclass(frozen=True)
class BoardResult:
    """A board operation result without leaking inaccessible post details."""

    accepted: bool
    reason: str
    message: Any | None = None


def _state() -> dict[str, Any]:
    """Read valid global board state, defaulting only missing historical keys."""
    state = ServerConfig.objects.conf(STATE_KEY) or {}
    if not isinstance(state, dict):
        return {"general_open": True, "next_general": 1, "next_news": 1}
    return {
        "general_open": state.get("general_open", True) is not False,
        "next_general": state.get("next_general", 1),
        "next_news": state.get("next_news", 1),
    }


def _metadata(message: Any) -> tuple[Any | None, dict[str, Any]]:
    """Return the post's deliberately unique tag metadata without trusting it."""
    tag = message.tags.get(
        f"board-{message.id}", category=BOARD_CATEGORY, return_tagobj=True
    )
    try:
        return tag, json.loads(tag.db_data) if tag and tag.db_data else {}
    except (TypeError, json.JSONDecodeError):
        return tag, {}


def metadata(message: Any) -> dict[str, Any]:
    """Expose safe persisted post facts for command rendering only."""
    return _metadata(message)[1]


def board_messages(board: str, *, visible_only: bool = True):
    """Return posts from precisely one of the two released logical boards."""
    posts = Msg.objects.filter(
        db_tags__db_key=BOARD_TAG, db_tags__db_category=BOARD_CATEGORY
    ).filter(db_tags__db_key=board, db_tags__db_category=BOARD_CATEGORY)
    if visible_only:
        posts = posts.exclude(
            db_tags__db_key=REMOVED_TAG, db_tags__db_category=BOARD_CATEGORY
        )
    return posts


def _post_number(message: Any) -> int:
    """Return a safe board-local number for an already validated post."""
    number = metadata(message).get("number")
    return number if isinstance(number, int) and number > 0 else 0


def posts(board: str) -> list[Any]:
    """Order visible posts with pinned news first, then newest board number."""
    rows = list(board_messages(board))
    return sorted(
        rows,
        key=lambda post: (
            not (board == NEWS and post.tags.has(PINNED_TAG, category=BOARD_CATEGORY)),
            -_post_number(post),
        ),
    )


def unread_count(account: Any, board: str) -> int:
    """Count visible posts newer than the account's board-specific high-water mark."""
    high_water = getattr(account.db, "board_read_high_water", {}) or {}
    seen = high_water.get(board, 0) if isinstance(high_water, dict) else 0
    seen = seen if isinstance(seen, int) else 0
    return sum(_post_number(post) > seen for post in posts(board))


def post(author: Any, board: str, subject_raw: Any, body_raw: Any) -> BoardResult:
    """Create one validated, numbered, locked post under the board state lock."""
    subject, body = normalize_subject(subject_raw), normalize_body(body_raw)
    if board not in BOARDS or subject is None or body is None:
        return BoardResult(False, "invalid")
    if board == NEWS and not author.check_permstring("Admin"):
        return BoardResult(False, "denied")
    with transaction.atomic():
        config, _ = ServerConfig.objects.select_for_update().get_or_create(
            db_key=STATE_KEY, defaults={"db_value": {}}
        )
        state = _state()
        if board == GENERAL and not state["general_open"]:
            return BoardResult(False, "closed")
        counter_key = f"next_{board}"
        number = state[counter_key] if isinstance(state[counter_key], int) else 1
        if number < 1:
            number = 1
        state[counter_key] = number + 1
        config.value = state
        message = Msg.objects.create_message(
            author,
            body,
            header=subject,
            locks=f"read:all();delete:id({author.id}) or perm(Admin);edit:perm(Admin)",
            tags=((BOARD_TAG, BOARD_CATEGORY), (board, BOARD_CATEGORY)),
        )
        message.tags.add(
            f"board-{message.id}",
            category=BOARD_CATEGORY,
            data=json.dumps(
                {
                    "author_id": author.id,
                    "author_name": author.key,
                    "board": board,
                    "number": number,
                    "removed_at": None,
                    "removed_by": None,
                    "reason": None,
                },
                sort_keys=True,
            ),
        )
    return BoardResult(True, "ok", message)


def read(account: Any, board: str, number: Any) -> BoardResult:
    """Read one visible post and advance this account's board high-water mark."""
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return BoardResult(False, "missing")
    message = next(
        (post for post in posts(board) if _post_number(post) == wanted), None
    )
    if message is None or not message.access(account, "read"):
        return BoardResult(False, "missing")
    high_water = dict(getattr(account.db, "board_read_high_water", {}) or {})
    high_water[board] = max(high_water.get(board, 0), wanted)
    account.db.board_read_high_water = high_water
    return BoardResult(True, "ok", message)


def remove(
    actor: Any, board: str, number: Any, *, reason: str | None = None
) -> BoardResult:
    """Hide a general author's own post or any post for an audited Admin."""
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return BoardResult(False, "missing")
    message = next(
        (post for post in posts(board) if _post_number(post) == wanted), None
    )
    if message is None:
        return BoardResult(False, "missing")
    author_id = metadata(message).get("author_id")
    administrator = actor.check_permstring("Admin")
    if not administrator and (board != GENERAL or author_id != actor.id):
        return BoardResult(False, "denied")
    if administrator and not reason:
        return BoardResult(False, "reason_required")
    message.tags.add(REMOVED_TAG, category=BOARD_CATEGORY)
    tag, details = _metadata(message)
    if tag:
        details.update(
            removed_at=timezone.now().isoformat(), removed_by=actor.id, reason=reason
        )
        tag.db_data = json.dumps(details, sort_keys=True)
        tag.save(update_fields=["db_data"])
    return BoardResult(True, "ok", message)


def pin(actor: Any, number: Any, *, pinned: bool, reason: str) -> BoardResult:
    """Set a news pin only for an Admin with an audit reason."""
    if not actor.check_permstring("Admin") or not reason.strip():
        return BoardResult(False, "denied")
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return BoardResult(False, "missing")
    message = next((post for post in posts(NEWS) if _post_number(post) == wanted), None)
    if message is None:
        return BoardResult(False, "missing")
    if pinned:
        message.tags.add(PINNED_TAG, category=BOARD_CATEGORY)
    else:
        message.tags.remove(PINNED_TAG, category=BOARD_CATEGORY)
    return BoardResult(True, "ok", message)


def set_general_open(actor: Any, *, open_: bool, reason: str) -> BoardResult:
    """Auditably change only general posting; neither board's reading closes."""
    if not actor.check_permstring("Admin") or not reason.strip():
        return BoardResult(False, "denied")
    with transaction.atomic():
        config, _ = ServerConfig.objects.select_for_update().get_or_create(
            db_key=STATE_KEY, defaults={"db_value": {}}
        )
        state = _state()
        state["general_open"] = open_
        config.value = state
    return BoardResult(True, "ok")


def purge_expired() -> int:
    """Apply general's visible bounds and retain removed audit content for 30 days."""
    now = timezone.now()
    removed = 0
    general_posts = posts(GENERAL)
    for post in general_posts[GENERAL_VISIBLE_LIMIT:]:
        remove_for_retention(post, now, "visible_limit")
    for post in general_posts:
        if post.db_date_created <= now - GENERAL_RETENTION:
            remove_for_retention(post, now, "age_limit")
    for post in board_messages(GENERAL, visible_only=False).filter(
        db_tags__db_key=REMOVED_TAG, db_tags__db_category=BOARD_CATEGORY
    ) | board_messages(NEWS, visible_only=False).filter(
        db_tags__db_key=REMOVED_TAG, db_tags__db_category=BOARD_CATEGORY
    ):
        removed_at = metadata(post).get("removed_at")
        try:
            stamp = (
                datetime.fromisoformat(removed_at)
                if isinstance(removed_at, str)
                else None
            )
        except ValueError:
            stamp = None
        if stamp and stamp <= now - REMOVED_RETENTION:
            post.delete()
            removed += 1
    return removed


def remove_for_retention(message: Any, now: Any, reason: str) -> None:
    """Move a general post to its 30-day staff-only audit retention window."""
    if message.tags.has(REMOVED_TAG, category=BOARD_CATEGORY):
        return
    message.tags.add(REMOVED_TAG, category=BOARD_CATEGORY)
    tag, details = _metadata(message)
    if tag:
        details.update(removed_at=now.isoformat(), removed_by=None, reason=reason)
        tag.db_data = json.dumps(details, sort_keys=True)
        tag.save(update_fields=["db_data"])

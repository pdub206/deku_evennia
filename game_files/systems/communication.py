"""Shared, transient validation and abuse controls for player communication."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from time import monotonic
from typing import Any

MAX_COMMUNICATION_LENGTH = 500
RATE_WINDOW_SECONDS = 10.0
RATE_CAPACITY = 5


@dataclass(frozen=True)
class CommunicationResult:
    """Side-effect-free result of validating an authored communication action."""

    accepted: bool
    reason: str
    text: str = ""


def normalize_text(raw: Any) -> CommunicationResult:
    """Return one safe logical line, rejecting controls and Evennia markup.

    Pipes introduce Evennia's client formatting protocol.  Rejecting them keeps
    authored text from imitating server, channel, or privileged-system output.
    """
    if not isinstance(raw, str):
        return CommunicationResult(False, "invalid_text")
    text = unicodedata.normalize("NFC", raw)
    if "|" in text or any(unicodedata.category(char).startswith("C") for char in text):
        return CommunicationResult(False, "unsafe_text")
    text = " ".join(text.split())
    if not text:
        return CommunicationResult(False, "empty")
    if len(text) > MAX_COMMUNICATION_LENGTH:
        return CommunicationResult(False, "too_long")
    return CommunicationResult(True, "ok", text)


def _owner(actor: Any) -> Any:
    """Use the puppeting Account's runtime state, falling back for NPC adapters."""
    return getattr(actor, "account", None) or actor


def authorize(actor: Any, raw: Any, *, cost: int = 1) -> CommunicationResult:
    """Validate text and consume one transient per-account rate allowance.

    The bucket intentionally lives on ``ndb``: a server restart clears it and
    no speech attempt can turn into a queued persistent action.
    """
    result = normalize_text(raw)
    if not result.accepted:
        return result
    if not isinstance(cost, int) or cost < 1 or cost > RATE_CAPACITY:
        return CommunicationResult(False, "invalid_cost")
    owner = _owner(actor)
    now = monotonic()
    prior = getattr(owner.ndb, "communication_rate", ()) or ()
    timestamps = [
        stamp
        for stamp in prior
        if isinstance(stamp, float) and now - stamp < RATE_WINDOW_SECONDS
    ]
    if len(timestamps) + cost > RATE_CAPACITY:
        owner.ndb.communication_rate = timestamps
        return CommunicationResult(False, "rate_limited")
    owner.ndb.communication_rate = [*timestamps, *([now] * cost)]
    return result


def ignored_by(recipient: Any, sender: Any) -> bool:
    """Read COMM-01B's future account-id ignore record safely when present."""
    account = getattr(recipient, "account", None) or recipient
    sender_account = getattr(sender, "account", None) or sender
    ignored = getattr(getattr(account, "db", None), "ignored_account_ids", ()) or ()
    try:
        return getattr(sender_account, "id", None) in {int(value) for value in ignored}
    except (TypeError, ValueError):
        return False

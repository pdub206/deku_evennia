"""ITEM-05A item decay on the WORLD-01 ``objects`` lane.

A builder authors ``decay_minutes`` on an Item. The first objects pulse that
sees it creates one versioned ``item_decay`` record; each fresh token then
consumes one remaining pulse while the item is in the live world. There are no
per-item Scripts. Tokens persist before dispatch, so a hot reload keeps the
remaining count and cold downtime never counts or catches up.

On expiry the item's direct contents move into its parent in stable order,
keeping their own subtrees and timers, and only the outer item is deleted. A
failed spill leaves an ``expiring`` item to retry on the next token; nothing is
deleted or duplicated. Keys, money, and account-bound items do not decay.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

from evennia.utils import logger
from systems.equipment import clear_equipped_state
from systems.item_transfer import (
    ACCOUNT_BOUND_ATTRIBUTE,
    holder_of,
    record_audit,
)
from systems.pulses import PulseEvent, PulseLane, configured_cadences

DECAY_POLICY_ATTRIBUTE = "decay_minutes"
DECAY_ATTRIBUTE = "item_decay"
DECAY_VERSION = 1
MAX_DECAY_MINUTES = 10080
QUARANTINE_TAG = "quarantined"
QUARANTINE_CATEGORY = "item_decay"
_STATUSES = frozenset({"active", "paused", "expiring"})
_EXEMPT_TYPES = frozenset({"key", "money"})
_MAX_DEPTH = 64


class ItemDecayError(ValueError):
    """Malformed decay data or a failed, recoverable decay."""


@dataclass(frozen=True)
class DecayPulseResult:
    """Summary of one objects-lane decay pass."""

    processed: int
    decayed: int
    failures: int


def validate_decay_minutes(value: Any) -> int | None:
    """Accept whole minutes from 1 to one week, or explicit absence."""
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_DECAY_MINUTES
    ):
        raise ItemDecayError(
            f"Decay minutes must be a whole number from 1 to {MAX_DECAY_MINUTES}."
        )
    return value


def decay_pulses(minutes: int) -> int:
    """Convert authored minutes to whole objects-lane tokens, rounding up."""
    from django.conf import settings

    cadence = configured_cadences()[PulseLane.OBJECTS]
    interval = getattr(settings, "GAME_PULSE_INTERVAL_SECONDS", 1)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        raise ItemDecayError("Pulse interval settings are invalid.")
    return int(
        (Decimal(minutes) * 60 / Decimal(cadence * interval)).to_integral_value(
            ROUND_CEILING
        )
    )


def decay_record(item: Any) -> dict[str, Any] | None:
    """Read the exact supported record, or None before its first pulse."""
    raw = item.attributes.get(DECAY_ATTRIBUTE)
    if raw is None:
        return None
    expected = {"version", "policy_minutes", "remaining_pulses", "last_pulse", "status"}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ItemDecayError("Item decay record has an invalid shape.")
    if raw["version"] != DECAY_VERSION or isinstance(raw["version"], bool):
        raise ItemDecayError("Item decay record has an unsupported version.")
    if raw["status"] not in _STATUSES:
        raise ItemDecayError("Item decay status is invalid.")
    validate_decay_minutes(raw["policy_minutes"])
    for name in ("remaining_pulses", "last_pulse"):
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ItemDecayError("Item decay counters are invalid.")
    if raw["remaining_pulses"] == 0 and raw["status"] != "expiring":
        raise ItemDecayError("Item decay lifetime is invalid.")
    return dict(raw)


def decays(item: Any) -> bool:
    """Keys, money, and bound items are exempt from decay in this milestone."""
    return (
        str(item.attributes.get("type") or "") not in _EXEMPT_TYPES
        and item.attributes.get(ACCOUNT_BOUND_ATTRIBUTE) is not True
    )


def in_world(item: Any) -> bool:
    """Whether the item's outermost holder sits in a room (not stowed/limbo)."""
    current, depth = item.location, 0
    while current is not None and depth <= _MAX_DEPTH:
        if current.location is None:
            return current.is_typeclass("evennia.objects.objects.DefaultRoom")
        current, depth = current.location, depth + 1
    return False


def set_decay_policy(item: Any, minutes: int | None) -> None:
    """Builder seam: author or clear decay; a change starts a fresh timer."""
    minutes = validate_decay_minutes(minutes)
    item.attributes.remove(DECAY_ATTRIBUTE)
    item.tags.remove(QUARANTINE_TAG, category=QUARANTINE_CATEGORY)
    if minutes is None:
        item.attributes.remove(DECAY_POLICY_ATTRIBUTE)
    else:
        item.attributes.add(DECAY_POLICY_ATTRIBUTE, minutes)


def repair_decay(item: Any, actor: Any, reason: str) -> None:
    """Staff seam: drop a quarantined/stuck record so the authored policy restarts."""
    if not isinstance(reason, str) or not reason.strip():
        raise ItemDecayError("A staff repair needs a reason.")
    before = item.attributes.get(DECAY_ATTRIBUTE)
    item.attributes.remove(DECAY_ATTRIBUTE)
    item.tags.remove(QUARANTINE_TAG, category=QUARANTINE_CATEGORY)
    record_audit(
        item,
        actor,
        "decay repair",
        dict(before) if isinstance(before, Mapping) else repr(before),
        None,
        reason.strip(),
    )


def process_decay_pulse(event: PulseEvent) -> DecayPulseResult:
    """Consume one objects token per live decaying Item, isolating failures."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.OBJECTS:
        raise ItemDecayError("Item decay requires an objects-lane pulse event.")
    from typeclasses.objects import Item

    ids = list(
        Item.objects.filter_family(
            db_attributes__db_key__in=(DECAY_POLICY_ATTRIBUTE, DECAY_ATTRIBUTE)
        )
        .order_by("id")
        .values_list("id", flat=True)
        .distinct()
    )
    processed = decayed = failures = 0
    for item_id in ids:
        item = Item.objects.filter_family(id=item_id).first()
        if item is None or item.tags.has(QUARANTINE_TAG, category=QUARANTINE_CATEGORY):
            continue
        try:
            processed += 1
            if _advance(item, event.sequence):
                decayed += 1
        except Exception:
            failures += 1
            item.tags.add(QUARANTINE_TAG, category=QUARANTINE_CATEGORY)
            logger.log_trace(
                f"Item decay quarantined object #{item_id} at objects token "
                f"{event.sequence}."
            )
    return DecayPulseResult(processed, decayed, failures)


def _advance(item: Any, token: int) -> bool:
    """Apply one token to one item and return whether it decayed."""
    policy = validate_decay_minutes(item.attributes.get(DECAY_POLICY_ATTRIBUTE))
    record = decay_record(item)
    if policy is None or not decays(item):
        if record is not None and record["status"] != "expiring":
            item.attributes.remove(DECAY_ATTRIBUTE)
        return False
    if record is None:
        _write(item, policy, decay_pulses(policy), token, _live_status(item))
        return False
    if record["policy_minutes"] != policy:
        raise ItemDecayError("Item decay record disagrees with its authored policy.")
    if token <= record["last_pulse"]:
        return False
    if record["status"] == "expiring":
        _write(item, policy, 0, token, "expiring")
        return _decay(item)
    status = _live_status(item)
    remaining = record["remaining_pulses"] - (status == "active")
    if remaining:
        _write(item, policy, remaining, token, status)
        return False
    # Persist expiry first: a failed spill must stay recoverable, not restart.
    _write(item, policy, 0, token, "expiring")
    return _decay(item)


def _live_status(item: Any) -> str:
    """Timers pause while the item is outside the live world."""
    return "active" if in_world(item) else "paused"


def _write(item: Any, policy: int, remaining: int, token: int, status: str) -> None:
    """Persist the complete primitive record in one Attribute write."""
    item.attributes.add(
        DECAY_ATTRIBUTE,
        {
            "version": DECAY_VERSION,
            "policy_minutes": policy,
            "remaining_pulses": remaining,
            "last_pulse": token,
            "status": status,
        },
    )


def _decay(item: Any) -> bool:
    """Spill direct contents into the parent, then delete only the outer item."""
    parent = item.location
    if parent is None:
        raise ItemDecayError("A decaying item has nowhere to spill its contents.")
    holder = holder_of(parent)
    if item.attributes.get("worn_location"):
        if holder is not None and item.location is holder:
            holder.equipment.unequip(item)
        else:
            clear_equipped_state(item)
    for content in sorted(item.contents, key=lambda obj: (obj.key.casefold(), obj.id)):
        if not content.move_to(
            parent,
            quiet=True,
            move_type="item_decay",
            item_decay=True,
            encumbrance_bypass="item decay spill",
        ):
            logger.log_err(
                f"Item decay could not spill #{content.id} from #{item.id}; "
                "retrying on the next objects token."
            )
            return False
    name = item.key
    room = parent if holder is None else None
    while room is not None and room.location is not None:
        room = room.location
    if not item.delete():
        raise ItemDecayError(f"Item #{item.id} could not be deleted after decay.")
    try:
        if holder is not None:
            holder.msg(f"Your {name} decays away.")
        elif room is not None:
            room.msg_contents(f"{name[0].upper()}{name[1:]} decays away.")
    except Exception:
        logger.log_err("Item decay message delivery failed after deletion.")
    return True

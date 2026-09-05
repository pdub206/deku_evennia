"""COMBAT-05 corpse creation, access control, looting, and decay.

Corpse data is kept on the corpse object itself and physical possessions remain
ordinary Evennia contents.  This deliberately avoids a second inventory model:
the service only controls when those contents may enter or leave the container.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from evennia import create_object
from evennia.utils import logger
from systems.encumbrance import can_receive
from systems.pulses import PulseEvent, PulseLane

CORPSE_ATTRIBUTE = "corpse_state"
CORPSE_VERSION = 1
CURRENCY_ATTRIBUTE = "currency"
DEFAULT_NPC_DECAY_MINUTES = 10
DEFAULT_PC_DECAY_MINUTES = 30


class CorpseError(ValueError):
    """Raised when a corpse operation cannot safely use persistent state."""


@dataclass(frozen=True)
class CorpseRecord:
    """Validated primitive state for one independently decaying corpse."""

    death_id: str
    is_pc: bool
    owner_id: int | None
    display_name: str
    creation_location_id: int
    remaining_pulses: int
    last_pulse: int = 0
    currency: int = 0
    currency_transferred: bool = False
    status: str = "creating"


@dataclass(frozen=True)
class LootResult:
    """One requested physical withdrawal and its player-safe outcome."""

    item: Any
    moved: bool
    message: str = ""


@dataclass(frozen=True)
class CorpsePulseResult:
    """Summary of one durable corpse-lane pass."""

    processed: int
    decayed: int
    failures: int


def corpse_record(corpse: Any) -> CorpseRecord:
    """Read one exact supported record instead of silently accepting bad data."""
    raw = corpse.attributes.get(CORPSE_ATTRIBUTE)
    expected = {
        "version",
        "death_id",
        "is_pc",
        "owner_id",
        "display_name",
        "creation_location_id",
        "remaining_pulses",
        "last_pulse",
        "currency",
        "currency_transferred",
        "status",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CorpseError("Corpse record is invalid.")
    if raw.get("version") != CORPSE_VERSION:
        raise CorpseError("Corpse record has an unsupported version.")
    death_id, display_name, status = raw["death_id"], raw["display_name"], raw["status"]
    if not all(isinstance(value, str) and value for value in (death_id, display_name)):
        raise CorpseError("Corpse identity is invalid.")
    if status not in {"creating", "ready", "expiring"}:
        raise CorpseError("Corpse status is invalid.")
    is_pc = raw["is_pc"]
    owner_id = raw["owner_id"]
    if (
        not isinstance(is_pc, bool)
        or (
            is_pc
            and (
                isinstance(owner_id, bool)
                or not isinstance(owner_id, int)
                or owner_id < 1
            )
        )
        or (not is_pc and owner_id is not None)
    ):
        raise CorpseError("Corpse ownership is invalid.")
    integers = (
        raw["creation_location_id"],
        raw["remaining_pulses"],
        raw["last_pulse"],
        raw["currency"],
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in integers
    ):
        raise CorpseError("Corpse counters are invalid.")
    if raw["creation_location_id"] < 1 or raw["remaining_pulses"] < 0 or (
        raw["remaining_pulses"] == 0 and status != "expiring"
    ):
        raise CorpseError("Corpse location or lifetime is invalid.")
    if not isinstance(raw["currency_transferred"], bool):
        raise CorpseError("Corpse currency state is invalid.")
    return CorpseRecord(
        death_id,
        is_pc,
        owner_id,
        display_name,
        raw["creation_location_id"],
        raw["remaining_pulses"],
        raw["last_pulse"],
        raw["currency"],
        raw["currency_transferred"],
        status,
    )


def create_corpse(owner: Any, death_id: str) -> Any | None:
    """Create or resume exactly one corpse transaction for a final death.

    A failed item move leaves a visible ``creating`` corpse.  Retrying the
    same death identity resumes from the character's remaining contents rather
    than duplicating prior moves or currency.
    """
    if not isinstance(death_id, str) or not death_id:
        raise CorpseError("A corpse requires a non-empty death identity.")
    existing = _find_by_death_id(death_id)
    if existing is not None:
        return _complete_creation(existing, owner)
    location = getattr(owner, "location", None)
    location_id = getattr(location, "id", None)
    if location is None or not isinstance(location_id, int) or location_id < 1:
        logger.log_err(
            f"Cannot create corpse for #{getattr(owner, 'id', '?')} / {death_id}: "
            "the deceased has no valid location."
        )
        return None
    is_pc = _is_pc(owner)
    record = CorpseRecord(
        death_id=death_id,
        is_pc=is_pc,
        owner_id=owner.id if is_pc else None,
        display_name=owner.get_display_name(owner),
        creation_location_id=location_id,
        remaining_pulses=_lifetime_pulses(owner, is_pc),
    )
    corpse = create_object(
        "typeclasses.objects.Corpse", key=f"corpse of {owner.key}", nohome=True
    )
    _write(corpse, record)
    corpse.tags.add(death_id, category="corpse_death")
    if not corpse.move_to(
        location,
        quiet=True,
        move_type="corpse_creation",
        corpse_system=True,
        encumbrance_bypass="corpse creation",
    ):
        logger.log_err(f"Cannot place corpse #{corpse.id} for death {death_id}.")
        return corpse
    return _complete_creation(corpse, owner)


def can_withdraw(corpse: Any, looter: Any) -> bool:
    """Return the shared, side-effect-free PC/NPC corpse removal decision."""
    record = corpse_record(corpse)
    if not record.is_pc:
        return True
    return bool(
        getattr(looter, "id", None) == record.owner_id
        or getattr(looter, "is_superuser", False)
        or looter.check_permstring("Admin")
    )


def inspect_corpse(corpse: Any, looker: Any) -> tuple[Any, ...]:
    """Return public visible top-level contents in a deterministic order."""
    corpse_record(corpse)
    return tuple(sorted(corpse.contents, key=_content_sort_key))


def withdraw(corpse: Any, looter: Any, item: Any) -> LootResult:
    """Move one top-level corpse item through canonical capacity admission."""
    _require_current_content(corpse, item)
    if not can_withdraw(corpse, looter):
        return LootResult(
            item, False, "You are not allowed to remove anything from that corpse."
        )
    admission = can_receive(looter, item)
    if not admission.allowed:
        return LootResult(item, False, admission.message or "You cannot carry that.")
    if not item.move_to(
        looter,
        quiet=True,
        move_type="corpse_loot",
        capacity_actor=looter,
        corpse_withdrawal=True,
    ):
        return LootResult(item, False, "That cannot be removed from the corpse.")
    item.at_get(looter)
    return LootResult(item, True)


def withdraw_many(corpse: Any, looter: Any) -> tuple[LootResult, ...]:
    """Attempt each visible top-level item independently in stable order."""
    if not can_withdraw(corpse, looter):
        return tuple(
            LootResult(
                item, False, "You are not allowed to remove anything from that corpse."
            )
            for item in inspect_corpse(corpse, looter)
        )
    return tuple(
        withdraw(corpse, looter, item) for item in inspect_corpse(corpse, looter)
    )


def transfer_currency(corpse: Any, looter: Any) -> int:
    """Atomically move the minimal future-economy currency seam to a looter."""
    record = corpse_record(corpse)
    if not can_withdraw(corpse, looter):
        raise CorpseError("This corpse's contents are protected.")
    amount = record.currency
    if not amount:
        return 0
    current = _currency(looter)
    _set_currency(looter, current + amount)
    _write(corpse, replace(record, currency=0))
    return amount


def process_corpse_pulse(event: PulseEvent) -> CorpsePulseResult:
    """Consume each corpse-lane token once and spill expired contents safely."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.CORPSES:
        raise CorpseError("Corpse decay requires a corpse-lane pulse event.")
    from typeclasses.objects import Corpse

    processed = decayed = failures = 0
    for corpse in Corpse.objects.filter_family().iterator():
        try:
            record = corpse_record(corpse)
            processed += 1
            if record.last_pulse >= event.sequence:
                continue
            next_record = replace(
                record,
                last_pulse=event.sequence,
                remaining_pulses=record.remaining_pulses - 1,
            )
            if next_record.remaining_pulses:
                _write(corpse, next_record)
                continue
            _write(corpse, replace(next_record, status="expiring"))
            _decay(corpse)
            decayed += 1
        except Exception:
            failures += 1
            logger.log_trace(
                f"Corpse pulse failed for object #{getattr(corpse, 'id', '?')} "
                f"at token {event.sequence}."
            )
    return CorpsePulseResult(processed, decayed, failures)


def repair_corpse(corpse: Any, record: CorpseRecord) -> CorpseRecord:
    """Explicit staff-only seam for replacing malformed persistent corpse data."""
    if not isinstance(record, CorpseRecord):
        raise CorpseError("A repair requires a validated CorpseRecord.")
    _write(corpse, record)
    return record


def _complete_creation(corpse: Any, owner: Any) -> Any:
    """Resume a recoverable transfer without re-moving completed assets."""
    record = corpse_record(corpse)
    if record.status == "ready":
        return corpse
    if corpse.location is None:
        logger.log_err(f"Corpse #{corpse.id} has no location while being created.")
        return corpse
    if not record.currency_transferred:
        amount = _currency(owner)
        _set_currency(owner, 0)
        record = replace(
            record, currency=record.currency + amount, currency_transferred=True
        )
        _write(corpse, record)
    owner.equipment.unequip_all()
    for item in tuple(owner.contents):
        if item.location is not owner:
            continue
        if not item.move_to(
            corpse,
            quiet=True,
            move_type="corpse_transfer",
            corpse_transfer=True,
            encumbrance_bypass="corpse death transfer",
        ):
            raise CorpseError(f"Could not transfer {item.key} into a corpse.")
    _write(corpse, replace(corpse_record(corpse), status="ready"))
    return corpse


def _decay(corpse: Any) -> None:
    """Spill all remaining assets before deleting one expired corpse."""
    record = corpse_record(corpse)
    location = corpse.location
    if location is None:
        raise CorpseError("An expired corpse has no room for its contents.")
    for item in tuple(corpse.contents):
        if not item.move_to(
            location,
            quiet=True,
            move_type="corpse_decay",
            corpse_decay=True,
            encumbrance_bypass="corpse decay spill",
        ):
            raise CorpseError(f"Could not spill {item.key} from an expired corpse.")
    if record.currency:
        _set_currency(location, _currency(location) + record.currency)
        _write(corpse, replace(record, currency=0))
    location.msg_contents(f"The corpse of {record.display_name} decays away.")
    corpse.delete()


def _find_by_death_id(death_id: str) -> Any | None:
    """Find the sole known corpse for an idempotent death identity."""
    from typeclasses.objects import Corpse

    matches = []
    for corpse in Corpse.objects.filter_family().iterator():
        try:
            if corpse_record(corpse).death_id == death_id:
                matches.append(corpse)
        except CorpseError:
            continue
    if len(matches) > 1:
        raise CorpseError(f"More than one corpse exists for death identity {death_id}.")
    return matches[0] if matches else None


def _write(corpse: Any, record: CorpseRecord) -> None:
    """Persist the complete versioned primitive payload in one Attribute write."""
    corpse.attributes.add(
        CORPSE_ATTRIBUTE,
        {
            "version": CORPSE_VERSION,
            "death_id": record.death_id,
            "is_pc": record.is_pc,
            "owner_id": record.owner_id,
            "display_name": record.display_name,
            "creation_location_id": record.creation_location_id,
            "remaining_pulses": record.remaining_pulses,
            "last_pulse": record.last_pulse,
            "currency": record.currency,
            "currency_transferred": record.currency_transferred,
            "status": record.status,
        },
    )


def _lifetime_pulses(owner: Any, is_pc: bool) -> int:
    """Convert policy minutes to whole corpse-lane tokens without rounding down."""
    minutes: Any = getattr(
        settings, "PC_CORPSE_DECAY_MINUTES", DEFAULT_PC_DECAY_MINUTES
    )
    if not is_pc:
        minutes = owner.attributes.get(
            "corpse_decay_minutes",
            getattr(settings, "NPC_CORPSE_DECAY_MINUTES", DEFAULT_NPC_DECAY_MINUTES),
        )
    try:
        value = Decimal(str(minutes))
    except (InvalidOperation, ValueError):
        raise CorpseError(
            "Corpse lifetime must be a positive number of minutes."
        ) from None
    if not value.is_finite() or value <= 0:
        raise CorpseError("Corpse lifetime must be a positive number of minutes.")
    cadence = getattr(settings, "GAME_PULSE_CADENCES", {}).get("corpses", 60)
    interval = getattr(settings, "GAME_PULSE_INTERVAL_SECONDS", 1)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (cadence, interval)
    ):
        raise CorpseError("Corpse pulse settings are invalid.")
    return int(
        (value * 60 / Decimal(cadence * interval)).to_integral_value(ROUND_CEILING)
    )


def _is_pc(owner: Any) -> bool:
    """Match COMBAT-04's default-PC convention without a typeclass dependency."""
    value = owner.attributes.get("is_player_character")
    return True if value is None else bool(value)


def _currency(owner: Any) -> int:
    """Read the deliberately small currency seam, rejecting corrupt values."""
    value = owner.attributes.get(CURRENCY_ATTRIBUTE, default=0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CorpseError("Currency must be a non-negative whole number.")
    return value


def _set_currency(owner: Any, amount: int) -> None:
    """Write a validated currency amount through the same future-economy seam."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise CorpseError("Currency must be a non-negative whole number.")
    owner.attributes.add(CURRENCY_ATTRIBUTE, amount)


def _content_sort_key(item: Any) -> tuple[str, int]:
    """Keep bulk loot deterministic while preserving separate identical objects."""
    return (str(item.key).casefold(), int(getattr(item, "id", 0) or 0))


def _require_current_content(corpse: Any, item: Any) -> None:
    """Reject stale or nested selections before any authorization or movement."""
    corpse_record(corpse)
    if item.location is not corpse:
        raise CorpseError("That item is no longer in this corpse.")

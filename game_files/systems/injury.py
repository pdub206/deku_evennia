"""Canonical COMBAT-04 vitality, unconsciousness, and death-save rules.

Only this module may turn HP loss or healing into an injury transition.  The
record deliberately contains primitives only, so it survives reloads and is
safe for later corpse, respawn, and reward consumers to inspect.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from evennia.utils import logger
from systems.action_policy import Position
from systems.dice import roll
from systems.pulses import PulseEvent, PulseLane

INJURY_ATTRIBUTE = "injury_state"
INJURY_VERSION = 1


class InjuryError(ValueError):
    """An injury operation or its persisted record is invalid."""


class InjuryState(str, Enum):
    """Stable consciousness states imposed by the injury system."""

    CONSCIOUS = "conscious"
    DYING = "dying"
    INCAPACITATED = "incapacitated"
    DEAD = "dead"


@dataclass(frozen=True)
class InjuryRecord:
    """Validated durable injury data, detached from Evennia Attributes."""

    state: InjuryState
    successes: int = 0
    failures: int = 0
    last_recovery: int = 0
    death_id: str | None = None


@dataclass(frozen=True)
class InjuryResult:
    """Immutable evidence from one vitality transition or attempted transition."""

    accepted: bool
    previous_hp: int
    resulting_hp: int
    previous_state: InjuryState
    state: InjuryState
    successes: int = 0
    failures: int = 0
    die_roll: int | None = None
    check_total: int | None = None
    reason: str = ""
    combat_cleanup_required: bool = False
    death_id: str | None = None


@dataclass(frozen=True)
class InjuryPulseResult:
    """Summary of one recovery-lane death-save pass."""

    processed: int
    saves: int
    failures: int


def injury_record(owner: Any) -> InjuryRecord:
    """Return the owner's validated injury record, creating a safe default."""
    raw = owner.attributes.get(INJURY_ATTRIBUTE)
    if raw is None:
        record = InjuryRecord(InjuryState.CONSCIOUS)
        _write(owner, record)
        return record
    if not isinstance(raw, Mapping) or raw.get("version") != INJURY_VERSION:
        raise InjuryError("Injury record is invalid.")
    if set(raw) != {
        "version",
        "state",
        "successes",
        "failures",
        "last_recovery",
        "death_id",
    }:
        raise InjuryError("Injury record has unsupported fields.")
    try:
        state = InjuryState(raw["state"])
    except (TypeError, ValueError) as exc:
        raise InjuryError("Injury state is invalid.") from exc
    successes, failures, last = raw["successes"], raw["failures"], raw["last_recovery"]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (successes, failures, last)
        )
        or successes > 3
        or failures > 3
    ):
        raise InjuryError("Injury counters are invalid.")
    death_id = raw["death_id"]
    if death_id is not None and (not isinstance(death_id, str) or not death_id):
        raise InjuryError("Injury death identity is invalid.")
    if state in {InjuryState.CONSCIOUS, InjuryState.INCAPACITATED} and (
        successes or failures
    ):
        raise InjuryError("Stable injury states cannot retain death saves.")
    if state is InjuryState.DEAD and death_id is None:
        raise InjuryError("Death requires a stable identity.")
    return InjuryRecord(state, successes, failures, last, death_id)


def imposed_position(owner: Any) -> Position | None:
    """Return the policy position contributed by a valid injury record."""
    state = injury_record(owner).state
    return {
        InjuryState.DYING: Position.DYING,
        InjuryState.INCAPACITATED: Position.INCAPACITATED,
        InjuryState.DEAD: Position.DEAD,
    }.get(state)


def apply_damage(
    owner: Any, amount: int, *, critical: bool = False, emit_messages: bool = True
) -> InjuryResult:
    """Apply contextual damage and atomically reconcile HP with injury state."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise InjuryError("Damage must be a non-negative integer.")
    record = injury_record(owner)
    previous_hp = owner.stats.hp_current
    if _is_staff_immune(owner):
        return _result(
            False, previous_hp, previous_hp, record, "immune", previous=record
        )
    if record.state is InjuryState.DEAD:
        return _result(False, previous_hp, previous_hp, record, "dead", previous=record)
    if amount == 0:
        return _result(
            True, previous_hp, previous_hp, record, "no_damage", previous=record
        )

    final_hp = owner.stats.take_damage(amount)
    next_record = record
    reason = "damaged"
    if previous_hp > 0 and final_hp == 0:
        if amount - previous_hp >= owner.stats.hp_max:
            next_record = _dead(record)
            reason = "massive_damage"
        elif _uses_death_saves(owner):
            next_record = InjuryRecord(InjuryState.DYING, 0, 0, record.last_recovery)
            reason = "reduced_to_zero"
        else:
            next_record = _dead(record)
            reason = "reduced_to_zero"
    elif final_hp == 0 and record.state is InjuryState.INCAPACITATED:
        next_record = _with_failure(record, 2 if critical else 1)
        reason = "destabilized"
    elif final_hp == 0 and record.state is InjuryState.DYING:
        next_record = _with_failure(record, 2 if critical else 1)
        reason = "death_save_failure"

    _write(owner, next_record)
    cleanup = next_record.state in {
        InjuryState.DYING,
        InjuryState.INCAPACITATED,
        InjuryState.DEAD,
    }
    result = _result(
        True, previous_hp, final_hp, next_record, reason, cleanup, previous=record
    )
    if emit_messages and result.state is not record.state:
        _announce(owner, result)
    return result


def apply_healing(
    owner: Any, amount: int, *, emit_messages: bool = True
) -> InjuryResult:
    """Apply healing; only a positive result can wake an unconscious target."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise InjuryError("Healing must be a non-negative integer.")
    record = injury_record(owner)
    previous_hp = owner.stats.hp_current
    if record.state is InjuryState.DEAD:
        return _result(False, previous_hp, previous_hp, record, "dead", previous=record)
    final_hp = owner.stats.heal(amount)
    next_record = record
    reason = "healed"
    if final_hp > 0 and record.state is not InjuryState.CONSCIOUS:
        next_record = InjuryRecord(
            InjuryState.CONSCIOUS, last_recovery=record.last_recovery
        )
        owner.db.position = Position.RESTING.value
        reason = "recovered"
    _write(owner, next_record)
    result = _result(True, previous_hp, final_hp, next_record, reason, previous=record)
    if emit_messages and reason == "recovered":
        _announce(owner, result)
    return result


def attempt_stabilization(
    healer: Any,
    target: Any,
    *,
    die_roller: Callable[[int], int] = roll,
    emit_messages: bool = True,
) -> InjuryResult:
    """Attempt the DC 10 Wisdom (Medicine) check for a visible dying target."""
    if not hasattr(healer, "stats") or not hasattr(target, "stats"):
        raise InjuryError("Stabilization requires character participants.")
    record = injury_record(target)
    previous_hp = target.stats.hp_current
    if (
        healer.location is None
        or healer.location is not target.location
        or injury_record(healer).state is not InjuryState.CONSCIOUS
        or record.state is not InjuryState.DYING
        or previous_hp != 0
    ):
        return _result(
            False, previous_hp, previous_hp, record, "invalid_target", previous=record
        )
    die_roll = die_roller(20)
    if (
        isinstance(die_roll, bool)
        or not isinstance(die_roll, int)
        or not 1 <= die_roll <= 20
    ):
        raise InjuryError("Medicine roller returned an invalid d20 result.")
    total = die_roll + healer.stats.skill_bonus("Medicine")
    if total >= 10:
        next_record = InjuryRecord(
            InjuryState.INCAPACITATED, last_recovery=record.last_recovery
        )
        reason = "stabilized"
    else:
        next_record = _with_failure(record, 1)
        reason = "stabilization_failed"
    _write(target, next_record)
    result = InjuryResult(
        True,
        previous_hp,
        previous_hp,
        record.state,
        next_record.state,
        next_record.successes,
        next_record.failures,
        die_roll,
        total,
        reason,
        next_record.state is InjuryState.DEAD,
        next_record.death_id,
    )
    if emit_messages:
        healer.msg(
            f"Medicine check: d20 {die_roll} + {healer.stats.skill_bonus('Medicine')} = {total}."
        )
        _announce(target, result)
    return result


def process_recovery_pulse(
    event: PulseEvent, *, die_roller: Callable[[int], int] = roll
) -> InjuryPulseResult:
    """Consume recovery tokens before each due death save for on-grid characters."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.RECOVERY:
        raise InjuryError("Death saves require a recovery-lane pulse event.")
    from typeclasses.characters import Character

    processed = saves = failures = 0
    for owner in (
        Character.objects.filter_family(db_location__isnull=False).distinct().iterator()
    ):
        try:
            record = injury_record(owner)
            processed += 1
            if (
                record.state is not InjuryState.DYING
                or record.last_recovery >= event.sequence
            ):
                continue
            # Persist first so an exception or reload cannot replay this save.
            pending = InjuryRecord(
                record.state,
                record.successes,
                record.failures,
                event.sequence,
                record.death_id,
            )
            _write(owner, pending)
            die_roll = die_roller(20)
            if (
                isinstance(die_roll, bool)
                or not isinstance(die_roll, int)
                or not 1 <= die_roll <= 20
            ):
                raise InjuryError("Death-save roller returned an invalid d20 result.")
            if die_roll == 20:
                result = apply_healing(owner, 1)
                result = InjuryResult(
                    True,
                    0,
                    result.resulting_hp,
                    InjuryState.DYING,
                    result.state,
                    0,
                    0,
                    die_roll,
                    reason="natural_twenty",
                    death_id=result.death_id,
                )
            elif die_roll == 1:
                next_record = _with_failure(pending, 2)
                _write(owner, next_record)
                result = _result(
                    True,
                    0,
                    0,
                    next_record,
                    "natural_one",
                    next_record.state is InjuryState.DEAD,
                    die_roll=die_roll,
                )
            elif die_roll >= 10:
                success = pending.successes + 1
                next_record = (
                    InjuryRecord(
                        InjuryState.INCAPACITATED, last_recovery=event.sequence
                    )
                    if success >= 3
                    else InjuryRecord(
                        InjuryState.DYING, success, pending.failures, event.sequence
                    )
                )
                _write(owner, next_record)
                result = _result(
                    True, 0, 0, next_record, "death_save_success", die_roll=die_roll
                )
            else:
                next_record = _with_failure(pending, 1)
                _write(owner, next_record)
                result = _result(
                    True,
                    0,
                    0,
                    next_record,
                    "death_save_failure",
                    next_record.state is InjuryState.DEAD,
                    die_roll=die_roll,
                )
            saves += 1
            _announce(owner, result)
        except Exception:
            failures += 1
            logger.log_trace(
                f"Injury recovery failed for object #{owner.id} at token {event.sequence}."
            )
    return InjuryPulseResult(processed, saves, failures)


def repair_injury(owner: Any, *, state: InjuryState | None = None) -> InjuryRecord:
    """Explicit staff recovery for malformed data, never an implicit repair path."""
    if state is None:
        state = (
            InjuryState.CONSCIOUS
            if owner.stats.hp_current > 0
            else InjuryState.INCAPACITATED
        )
    record = InjuryRecord(
        state, death_id=uuid4().hex if state is InjuryState.DEAD else None
    )
    _write(owner, record)
    return record


def _uses_death_saves(owner: Any) -> bool:
    """PCs opt in by default; NPC builders may explicitly request the same policy."""
    is_pc = owner.attributes.get("is_player_character")
    return True if is_pc is None else bool(is_pc) or bool(owner.db.death_saves)


def _is_staff_immune(owner: Any) -> bool:
    """Use effective permissions so Evennia's quell state is honored naturally."""
    return bool(
        getattr(owner, "is_superuser", False) or owner.check_permstring("Admin")
    )


def _dead(record: InjuryRecord) -> InjuryRecord:
    """Create one stable downstream identity at the irreversible boundary."""
    return InjuryRecord(
        InjuryState.DEAD,
        last_recovery=record.last_recovery,
        death_id=record.death_id or uuid4().hex,
    )


def _with_failure(record: InjuryRecord, amount: int) -> InjuryRecord:
    """Add failures and convert the third one into final death."""
    failures = min(3, record.failures + amount)
    if failures >= 3:
        return _dead(
            InjuryRecord(
                InjuryState.DYING,
                record.successes,
                failures,
                record.last_recovery,
                record.death_id,
            )
        )
    return InjuryRecord(
        InjuryState.DYING,
        record.successes,
        failures,
        record.last_recovery,
        record.death_id,
    )


def _write(owner: Any, record: InjuryRecord) -> None:
    """Persist the exact validated primitive payload."""
    owner.attributes.add(
        INJURY_ATTRIBUTE,
        {
            "version": INJURY_VERSION,
            "state": record.state.value,
            "successes": record.successes,
            "failures": record.failures,
            "last_recovery": record.last_recovery,
            "death_id": record.death_id,
        },
    )
    if record.state is InjuryState.DEAD:
        # Death identity is the cross-system contract: COMBAT-05 owns the
        # idempotent follow-up and isolates a recoverable corpse failure from
        # the irreversible injury transition.
        try:
            from systems.corpses import create_corpse

            corpse = create_corpse(owner, record.death_id or "")
            # COMBAT-06 cannot OOC/extract before COMBAT-05's transfer commits.
            from systems.respawn import final_death

            final_death(owner, corpse)
        except Exception:
            logger.log_trace(
                f"Corpse creation failed for object #{getattr(owner, 'id', '?')} "
                f"after final death."
            )


def _result(
    accepted: bool,
    previous_hp: int,
    resulting_hp: int,
    record: InjuryRecord,
    reason: str,
    cleanup: bool = False,
    *,
    die_roll: int | None = None,
    previous: InjuryRecord | None = None,
) -> InjuryResult:
    """Build one consistent result without exposing mutable storage."""
    return InjuryResult(
        accepted,
        previous_hp,
        resulting_hp,
        (previous or record).state,
        record.state,
        record.successes,
        record.failures,
        die_roll,
        reason=reason,
        combat_cleanup_required=cleanup,
        death_id=record.death_id,
    )


def _announce(owner: Any, result: InjuryResult) -> None:
    """Emit one private and room-local transition message for this operation."""
    messages = {
        "reduced_to_zero": "You fall unconscious and begin dying.",
        "massive_damage": "Massive damage kills you.",
        "stabilized": "You are stable but unconscious.",
        "recovered": "You regain consciousness.",
        "natural_twenty": "A remarkable surge restores you to consciousness.",
        "death_save_success": "You cling to life.",
        "death_save_failure": "You slip closer to death.",
        "natural_one": "Your condition sharply worsens.",
    }
    private = messages.get(result.reason, "")
    if result.state is InjuryState.DEAD:
        private = "You die."
    if private:
        owner.msg(private)
    room_messages = {
        "reduced_to_zero": f"{owner.key} falls unconscious.",
        "stabilized": f"{owner.key}'s condition stabilizes.",
        "recovered": f"{owner.key} regains consciousness.",
        "death_save_success": f"{owner.key}'s condition improves.",
        "death_save_failure": f"{owner.key}'s condition worsens.",
        "natural_one": f"{owner.key}'s condition sharply worsens.",
        "destabilized": f"{owner.key}'s condition worsens.",
    }
    if owner.location:
        room_message = (
            f"{owner.key} dies."
            if result.state is InjuryState.DEAD
            else room_messages.get(result.reason)
        )
        if room_message:
            owner.location.msg_contents(room_message, exclude=[owner], from_obj=owner)


def announce_transition(owner: Any, result: InjuryResult) -> None:
    """Render a previously returned transition once after its causing action."""
    if result.previous_state is not result.state or result.reason == "destabilized":
        _announce(owner, result)


def handle_ooc_departure(owner: Any) -> InjuryResult | None:
    """Apply the explicit OOC-at-zero rule without affecting disconnect saves."""
    try:
        record = injury_record(owner)
        if owner.stats.hp_current != 0 or record.state not in {
            InjuryState.DYING,
            InjuryState.INCAPACITATED,
        }:
            return None
        dead = _dead(record)
        _write(owner, dead)
        result = _result(True, 0, 0, dead, "ooc_death", True, previous=record)
        _announce(owner, result)
        return result
    except Exception:
        logger.log_trace(f"Injury OOC transition failed for object #{owner.id}.")
        return None

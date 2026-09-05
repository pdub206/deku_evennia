"""Pulse-driven mutable resources and their natural-recovery rules.

Resources deliberately expose a small interface: their values, a persistent
gain mutator, the amount to try on a recovery pulse, and any reason recovery
is blocked.  Class systems can add a resource without teaching the scheduler
about that resource's storage or rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from numbers import Real
from typing import Any, Iterable, Mapping, Protocol

from django.db import transaction
from evennia.utils import logger
from systems.action_policy import Position, resolve_position
from systems.pulses import PulseEvent, PulseLane

RECOVERY_TOKENS_ATTRIBUTE = "resource_recovery_tokens"
RECOVERY_TOKENS_VERSION = 1


class ResourceRecoveryError(ValueError):
    """A resource or its durable recovery bookkeeping is invalid."""


class RecoverySkipReason(str, Enum):
    """Stable reasons a recovery result did not increase a resource."""

    DUPLICATE = "duplicate"
    OFF_GRID = "off_grid"
    FIGHTING = "fighting"
    INCAPACITATED = "incapacitated"
    DYING = "dying"
    DEAD = "dead"
    ZERO = "zero"
    FULL = "full"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class ResourceRecoveryResult:
    """The complete outcome for one resource on one recovery-lane token."""

    resource_key: str
    previous_value: int
    attempted_gain: int
    final_value: int
    skip_reason: RecoverySkipReason | None = None


@dataclass(frozen=True)
class RecoveryPulseResult:
    """Summary of one recovery event across independent character owners."""

    processed: int
    recovered: int
    skipped: int
    failures: int


class RecoverableResource(Protocol):
    """Contract implemented by a resource that participates in recovery."""

    key: str

    def current_value(self, owner: Any) -> int:
        """Return the current persistent resource value."""

    def maximum_value(self, owner: Any) -> int:
        """Return the effective maximum resource value."""

    def apply_gain(self, owner: Any, amount: int) -> int:
        """Persist a non-negative gain and return the resulting value."""

    def set_current_value(self, owner: Any, value: int) -> int:
        """Persist an exact valid current value for transactional restoration."""

    def recovery_gain(self, owner: Any, posture: Position) -> int:
        """Return this owner's non-negative natural gain for one pulse."""


_POSTURE_MULTIPLIERS = {
    Position.STANDING: Fraction(1),
    Position.SITTING: Fraction(5, 4),
    Position.RESTING: Fraction(3, 2),
    Position.SLEEPING: Fraction(2),
}


def recovery_modifier_total(owner: Any, resource_key: str) -> int:
    """Return numeric ``recovery:<resource>`` sources without effect coupling."""
    sources = getattr(owner, "get_stat_modifier_sources", None)
    if not callable(sources):
        return 0
    total = 0
    for modifiers in sources():
        if not isinstance(modifiers, Mapping):
            continue
        value = modifiers.get(f"recovery:{resource_key}", 0)
        if isinstance(value, bool) or not isinstance(value, Real):
            continue
        total += int(value)
    return total


class HpResource:
    """The canonical hit-point resource supplied by RULES-01."""

    key = "hp"

    def current_value(self, owner: Any) -> int:
        """Return HP through the canonical clamping accessor."""
        return owner.stats.hp_current

    def maximum_value(self, owner: Any) -> int:
        """Return the current derived HP maximum."""
        return owner.stats.hp_max

    def apply_gain(self, owner: Any, amount: int) -> int:
        """Heal through the canonical RULES-01 mutator."""
        return owner.stats.heal(amount)

    def set_current_value(self, owner: Any, value: int) -> int:
        """Restore HP through the canonical RULES-01 mutator."""
        return owner.stats.set_hp(value)

    def recovery_gain(self, owner: Any, posture: Position) -> int:
        """Calculate HP recovery from level, Constitution, posture, and effects."""
        base = max(1, owner.stats.level + owner.stats.ability_modifier("Constitution"))
        adjusted = max(0, base + recovery_modifier_total(owner, self.key))
        multiplier = _POSTURE_MULTIPLIERS[posture]
        return (adjusted * multiplier.numerator) // multiplier.denominator


HP_RESOURCE = HpResource()


def recover_resource(
    owner: Any, resource: RecoverableResource, event: PulseEvent
) -> ResourceRecoveryResult:
    """Recover one resource atomically and consume its durable lane token.

    A consumed token is written for successes *and* ordinary skips.  This is
    what makes replaying a full or blocked pulse harmless after later damage.
    """
    _validate_recovery_event(event)
    _validate_resource(resource)
    with transaction.atomic():
        previous = _validated_value(resource.current_value(owner), "current value")
        mutated = False
        try:
            tokens = _read_recovery_tokens(owner)
            if tokens.get(resource.key, 0) >= event.sequence:
                result = ResourceRecoveryResult(
                    resource.key,
                    previous,
                    0,
                    previous,
                    RecoverySkipReason.DUPLICATE,
                )
            else:
                maximum = _validated_value(
                    resource.maximum_value(owner), "maximum value"
                )
                if previous > maximum:
                    raise ResourceRecoveryError(
                        "A resource current value exceeds its maximum."
                    )
                reason, posture = _recovery_block(owner, previous, maximum)
                if reason is not None:
                    result = ResourceRecoveryResult(
                        resource.key, previous, 0, previous, reason
                    )
                else:
                    attempted = _validated_gain(resource.recovery_gain(owner, posture))
                    if attempted == 0:
                        result = ResourceRecoveryResult(
                            resource.key,
                            previous,
                            0,
                            previous,
                            RecoverySkipReason.SUPPRESSED,
                        )
                    else:
                        # A third-party resource can mutate before reporting a
                        # failure, so mark it before entering the extension hook.
                        mutated = True
                        final = _validated_value(
                            resource.apply_gain(owner, attempted), "final value"
                        )
                        if final < previous or final > resource.maximum_value(owner):
                            raise ResourceRecoveryError(
                                "A resource gain produced an invalid value."
                            )
                        result = ResourceRecoveryResult(
                            resource.key, previous, attempted, final
                        )

            tokens[resource.key] = event.sequence
            _write_recovery_tokens(owner, tokens)
            return result
        except Exception:
            if mutated:
                resource.set_current_value(owner, previous)
            raise


def process_recovery_pulse(
    event: PulseEvent, resources: Iterable[RecoverableResource] = (HP_RESOURCE,)
) -> RecoveryPulseResult:
    """Recover every on-grid PC and NPC, isolating malformed owners.

    Recovery does not scan stowed/offline characters, so reconnecting cannot
    cause a catch-up heal.  Imports stay lazy to avoid a startup typeclass cycle.
    """
    _validate_recovery_event(event)
    from typeclasses.characters import Character

    resource_list = tuple(resources)
    for resource in resource_list:
        _validate_resource(resource)

    owners = Character.objects.filter_family(db_location__isnull=False).distinct()
    processed = recovered = skipped = failures = 0
    for owner in owners.iterator():
        try:
            outcomes = tuple(
                recover_resource(owner, resource, event) for resource in resource_list
            )
        except Exception:
            failures += 1
            logger.log_trace(
                "Recovery pulse "
                f"{event.sequence} failed for object #{owner.id} "
                f"during heartbeat {event.heartbeat}."
            )
            continue
        processed += 1
        recovered += sum(
            outcome.final_value > outcome.previous_value for outcome in outcomes
        )
        skipped += sum(outcome.skip_reason is not None for outcome in outcomes)
    return RecoveryPulseResult(processed, recovered, skipped, failures)


def _recovery_block(
    owner: Any, current: int, maximum: int
) -> tuple[RecoverySkipReason | None, Position | None]:
    """Return the first canonical reason this owner cannot recover."""
    if getattr(owner, "location", None) is None:
        return RecoverySkipReason.OFF_GRID, None
    resolution = resolve_position(owner)
    blocking = {
        Position.FIGHTING: RecoverySkipReason.FIGHTING,
        Position.INCAPACITATED: RecoverySkipReason.INCAPACITATED,
        Position.DYING: RecoverySkipReason.DYING,
        Position.DEAD: RecoverySkipReason.DEAD,
    }
    if resolution.position in blocking:
        return blocking[resolution.position], None
    if current == 0:
        return RecoverySkipReason.ZERO, None
    if current >= maximum:
        return RecoverySkipReason.FULL, None
    # An invalid posture resolves as incapacitated above. Remaining states retain
    # their voluntary posture, including stunned characters.
    if resolution.posture not in _POSTURE_MULTIPLIERS:
        raise ResourceRecoveryError("Recovery needs a valid voluntary posture.")
    return None, resolution.posture


def _read_recovery_tokens(owner: Any) -> dict[str, int]:
    """Read and validate the versioned per-resource recovery token payload."""
    stored = owner.attributes.get(RECOVERY_TOKENS_ATTRIBUTE)
    if stored is None:
        return {}
    if (
        not isinstance(stored, Mapping)
        or stored.get("version") != RECOVERY_TOKENS_VERSION
    ):
        raise ResourceRecoveryError("Resource recovery token data is invalid.")
    raw_tokens = stored.get("tokens")
    if not isinstance(raw_tokens, Mapping):
        raise ResourceRecoveryError("Resource recovery tokens are invalid.")
    tokens: dict[str, int] = {}
    for key, sequence in raw_tokens.items():
        if not isinstance(key, str) or not key or not key.replace("_", "").isalnum():
            raise ResourceRecoveryError("A resource recovery token key is invalid.")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ResourceRecoveryError(
                "A resource recovery token sequence is invalid."
            )
        tokens[key] = sequence
    return tokens


def _write_recovery_tokens(owner: Any, tokens: Mapping[str, int]) -> None:
    """Persist a detached, versioned token payload with the resource mutation."""
    owner.attributes.add(
        RECOVERY_TOKENS_ATTRIBUTE,
        {"version": RECOVERY_TOKENS_VERSION, "tokens": dict(tokens)},
    )


def _validate_recovery_event(event: PulseEvent) -> None:
    """Reject non-recovery or malformed scheduler events at the boundary."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.RECOVERY:
        raise ResourceRecoveryError("Recovery requires a recovery-lane pulse event.")
    if (
        isinstance(event.sequence, bool)
        or not isinstance(event.sequence, int)
        or event.sequence < 1
    ):
        raise ResourceRecoveryError("A recovery pulse sequence must be positive.")


def _validate_resource(resource: RecoverableResource) -> None:
    """Check the stable key and required methods of one resource extension."""
    key = getattr(resource, "key", None)
    if not isinstance(key, str) or not key or not key.replace("_", "").isalnum():
        raise ResourceRecoveryError("A resource needs a stable alphanumeric key.")
    for method in (
        "current_value",
        "maximum_value",
        "apply_gain",
        "set_current_value",
        "recovery_gain",
    ):
        if not callable(getattr(resource, method, None)):
            raise ResourceRecoveryError(f"Resource '{key}' lacks {method}().")


def _validated_value(value: Any, label: str) -> int:
    """Require a non-negative integer resource value."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResourceRecoveryError(
            f"A resource {label} must be a non-negative integer."
        )
    return value


def _validated_gain(value: Any) -> int:
    """Require a non-negative integer recovery amount."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResourceRecoveryError("A resource recovery gain must be non-negative.")
    return value

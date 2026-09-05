"""Shared scheduling primitives and live world-pulse consumers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from django.conf import settings
from evennia.utils import logger

PULSE_STATE_VERSION = 1


class PulseError(ValueError):
    """Base error for invalid pulse configuration or persistent state."""


class PulseLane(str, Enum):
    """Stable names for work scheduled by the global pulse service."""

    COMBAT = "combat"
    RECOVERY = "recovery"
    MOBILES = "mobiles"
    EFFECTS = "effects"
    CORPSES = "corpses"
    WORLD_TIME = "world_time"
    WEATHER = "weather"
    RESETS = "resets"


PULSE_LANES = tuple(PulseLane)

# Cadences are measured in scheduler heartbeats. These conservative defaults
# provide a stable contract now and remain independently tunable as consumers
# are implemented by their roadmap items.
DEFAULT_PULSE_CADENCES = MappingProxyType(
    {
        PulseLane.COMBAT: 2,
        PulseLane.RECOVERY: 60,
        PulseLane.MOBILES: 10,
        PulseLane.EFFECTS: 6,
        PulseLane.CORPSES: 60,
        PulseLane.WORLD_TIME: 60,
        PulseLane.WEATHER: 300,
        PulseLane.RESETS: 60,
    }
)


@dataclass(frozen=True)
class PulseEvent:
    """One due lane invocation with a persistent idempotency token."""

    heartbeat: int
    lane: PulseLane
    sequence: int


@dataclass(frozen=True)
class EffectPulseResult:
    """Summary of owners processed by one effect pulse."""

    processed: int
    removals: int
    failures: int


def initial_pulse_state() -> dict[str, Any]:
    """Return a fresh serializable scheduler-state payload."""
    return {
        "version": PULSE_STATE_VERSION,
        "heartbeat": 0,
        "lane_sequences": {lane.value: 0 for lane in PULSE_LANES},
    }


def configured_cadences() -> dict[PulseLane, int]:
    """Return validated per-lane cadence overrides from Django settings."""
    configured = getattr(settings, "GAME_PULSE_CADENCES", {})
    if not isinstance(configured, Mapping):
        raise PulseError("GAME_PULSE_CADENCES must be a mapping.")

    known_names = {lane.value for lane in PULSE_LANES}
    unknown_names = set(configured) - known_names
    if unknown_names:
        unknown = ", ".join(sorted(str(name) for name in unknown_names))
        raise PulseError(f"Unknown game pulse lane(s): {unknown}.")

    cadences: dict[PulseLane, int] = {}
    for lane in PULSE_LANES:
        cadence = configured.get(lane.value, DEFAULT_PULSE_CADENCES[lane])
        if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence < 1:
            raise PulseError(
                f"The '{lane.value}' pulse cadence must be a positive integer."
            )
        cadences[lane] = cadence
    return cadences


def advance_pulse_state(
    stored_state: Mapping[str, Any], cadences: Mapping[PulseLane, int]
) -> tuple[dict[str, Any], tuple[PulseEvent, ...]]:
    """Advance one heartbeat and return new state plus all due lane events."""
    heartbeat, lane_sequences = _validated_state(stored_state)
    heartbeat += 1
    events: list[PulseEvent] = []

    for lane in PULSE_LANES:
        try:
            cadence = cadences[lane]
        except (KeyError, TypeError) as err:
            raise PulseError(f"Missing cadence for pulse lane '{lane.value}'.") from err
        if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence < 1:
            raise PulseError(
                f"The '{lane.value}' pulse cadence must be a positive integer."
            )
        if heartbeat % cadence:
            continue
        lane_sequences[lane.value] += 1
        events.append(PulseEvent(heartbeat, lane, lane_sequences[lane.value]))

    state = {
        "version": PULSE_STATE_VERSION,
        "heartbeat": heartbeat,
        "lane_sequences": lane_sequences,
    }
    return state, tuple(events)


def process_effect_pulse(event: PulseEvent) -> EffectPulseResult:
    """Advance active effects for every PC and NPC, isolating owner failures."""
    # Import lazily to avoid a typeclass import cycle during Evennia startup.
    from systems.effects import EFFECTS_ATTRIBUTE
    from typeclasses.characters import Character

    owners = Character.objects.filter_family(
        db_attributes__db_key=EFFECTS_ATTRIBUTE
    ).distinct()
    processed = 0
    removals = 0
    failures = 0
    for owner in owners.iterator():
        try:
            removed = owner.effects.process_duration(1)
        except Exception:
            failures += 1
            logger.log_trace(
                "Effect pulse "
                f"{event.sequence} failed for object #{owner.id} "
                f"during heartbeat {event.heartbeat}."
            )
            continue
        processed += 1
        removals += len(removed)

    return EffectPulseResult(processed, removals, failures)


def process_resource_recovery_pulse(event: PulseEvent):
    """Dispatch RULES-04 recovery without adding a typeclass import cycle."""
    from systems.resources import process_recovery_pulse

    return process_recovery_pulse(event)


def _validated_state(
    stored_state: Mapping[str, Any],
) -> tuple[int, dict[str, int]]:
    """Validate and detach the persistent scheduler-state payload."""
    if not isinstance(stored_state, Mapping):
        raise PulseError("Game pulse state must be a mapping.")
    if stored_state.get("version") != PULSE_STATE_VERSION:
        raise PulseError("Game pulse state has an unsupported version.")

    heartbeat = stored_state.get("heartbeat")
    if isinstance(heartbeat, bool) or not isinstance(heartbeat, int) or heartbeat < 0:
        raise PulseError("Game pulse heartbeat must be a non-negative integer.")

    stored_sequences = stored_state.get("lane_sequences")
    if not isinstance(stored_sequences, Mapping):
        raise PulseError("Game pulse lane sequences must be a mapping.")
    expected_names = {lane.value for lane in PULSE_LANES}
    if set(stored_sequences) != expected_names:
        raise PulseError("Game pulse state does not contain the expected lanes.")

    lane_sequences: dict[str, int] = {}
    for lane in PULSE_LANES:
        sequence = stored_sequences[lane.value]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise PulseError(
                f"The '{lane.value}' pulse sequence must be a non-negative integer."
            )
        lane_sequences[lane.value] = sequence
    return heartbeat, lane_sequences

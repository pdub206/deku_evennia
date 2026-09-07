"""SRD-shaped rest completion for MAGIC-03 class-resource recovery.

The global recovery lane is the sole clock. At its default one-minute cadence,
10 uninterrupted resting/sleeping pulses represent an in-world hour and
complete a Short Rest. Eighty pulses, with at least 60 spent sleeping, complete
a Long Rest. This explicit MUD-time compression preserves the SRD 5.2.1 rest
structure without creating another timer or granting downtime catch-up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from evennia.utils import logger
from systems.action_policy import Position, resolve_position
from systems.magic_resources import MagicResourceError, recover_profile
from systems.pulses import PulseEvent, PulseLane

MAGIC_REST_ATTRIBUTE = "magic_rest_progress"
MAGIC_REST_VERSION = 3
SHORT_REST_PULSES = 10
LONG_REST_PULSES = 80
LONG_REST_SLEEP_PULSES = 60
SAFE_REST_TAG = "safe_rest"
SAFE_REST_TAG_CATEGORY = "room_feature"


class MagicRestError(ValueError):
    """A rest-completion record or recovery-lane request is invalid."""


@dataclass(frozen=True)
class MagicRestResult:
    """One owner's durable progress outcome for a recovery-lane event."""

    processed: bool
    profiles: tuple[str, ...] = ()
    restored: tuple[tuple[str, int], ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class MagicRestPulseResult:
    """Summary of the rest-completion extension on one recovery token."""

    processed: int
    completed: int
    interrupted: int
    failures: int


def advance_magic_rest(owner: Any, event: PulseEvent) -> MagicRestResult:
    """Advance one on-grid character's uninterrupted rest exactly once.

    Replayed lane tokens are ignored. A gap in sequence is treated as an
    interruption, which avoids granting rest credit for offline time, a stopped
    server, or a missed processor invocation.
    """
    _validate_event(event)
    state = _state(owner)
    if event.sequence <= state["last_sequence"]:
        return MagicRestResult(False, reason="duplicate")
    contiguous = event.sequence == state["last_sequence"] + 1
    eligible, sleeping = _rest_eligibility(owner)
    if not contiguous or not eligible:
        _write_state(
            owner,
            {
                "last_sequence": event.sequence,
                "continuous_pulses": 0,
                "sleep_pulses": 0,
            },
        )
        return MagicRestResult(
            True,
            reason="interrupted" if contiguous else "sequence_gap",
        )

    continuous = state["continuous_pulses"] + 1
    sleep_pulses = state["sleep_pulses"] + int(sleeping)
    profiles: list[str] = []
    restored: list[tuple[str, int]] = []
    short_pulses, long_pulses, long_sleep_pulses = _rest_durations()
    if continuous % short_pulses == 0:
        profiles.append("short_rest")
        restored.extend(recover_profile(owner, "short_rest"))
    if continuous >= long_pulses and sleep_pulses >= long_sleep_pulses:
        profiles.append("long_rest")
        restored.extend(recover_profile(owner, "long_rest"))
        continuous = 0
        sleep_pulses = 0
    _write_state(
        owner,
        {
            "last_sequence": event.sequence,
            "continuous_pulses": continuous,
            "sleep_pulses": sleep_pulses,
        },
    )
    return MagicRestResult(
        True,
        tuple(profiles),
        tuple(restored),
        "completed" if profiles else "progressing",
    )


def interrupt_magic_rest(owner: Any) -> None:
    """Discard in-progress rest credit after an immediate SRD interruption."""
    raw = owner.attributes.get(MAGIC_REST_ATTRIBUTE)
    if raw is None:
        return
    state = _state(owner)
    if not state["continuous_pulses"] and not state["sleep_pulses"]:
        return
    _write_state(
        owner,
        {
            "last_sequence": state["last_sequence"],
            "continuous_pulses": 0,
            "sleep_pulses": 0,
        },
    )


def process_magic_rest_pulse(event: PulseEvent) -> MagicRestPulseResult:
    """Advance rest only for on-grid characters, isolating malformed owners."""
    _validate_event(event)
    from typeclasses.characters import Character

    processed = completed = interrupted = failures = 0
    owners = Character.objects.filter_family(db_location__isnull=False).distinct()
    for owner in owners.iterator():
        try:
            result = advance_magic_rest(owner, event)
        except (MagicRestError, MagicResourceError):
            failures += 1
            logger.log_trace(
                "Magic rest processing failed for "
                f"#{owner.id} at recovery token {event.sequence}."
            )
            continue
        if result.processed:
            processed += 1
        completed += bool(result.profiles)
        interrupted += result.reason in {"interrupted", "sequence_gap"}
    return MagicRestPulseResult(processed, completed, interrupted, failures)


def _rest_eligibility(owner: Any) -> tuple[bool, bool]:
    """Return whether the current exact posture may advance a rest."""
    location = getattr(owner, "location", None)
    if (
        location is None
        or owner.stats.hp_current < 1
        or not _is_safe_rest_location(location)
    ):
        return False, False
    resolution = resolve_position(owner)
    if not resolution.valid or resolution.position not in {
        Position.RESTING,
        Position.SLEEPING,
    }:
        return False, False
    return True, resolution.position is Position.SLEEPING


def _is_safe_rest_location(location: Any) -> bool:
    """Allow only explicit safe-rest rooms and existing sanctuary rooms."""
    tags = getattr(location, "tags", None)
    if tags is None:
        return False
    if tags.has(SAFE_REST_TAG, category=SAFE_REST_TAG_CATEGORY):
        return True
    # Sanctuary already denotes a protected, player-safe room in COMBAT-06.
    # Reusing it prevents a second incompatible safety marker for that case.
    try:
        from systems.areas import AREA_TAG_CATEGORY

        return tags.has("sanctuary", category=AREA_TAG_CATEGORY)
    except Exception:
        return False


def _state(owner: Any) -> dict[str, int]:
    """Read detached primitive rest progress, rejecting unsupported records."""
    raw = owner.attributes.get(MAGIC_REST_ATTRIBUTE)
    if raw is None:
        return {
            "last_sequence": 0,
            "continuous_pulses": 0,
            "sleep_pulses": 0,
        }
    expected = {
        "version",
        "last_sequence",
        "continuous_pulses",
        "sleep_pulses",
    }
    v2_expected = expected | {"last_long_rest"}
    if not isinstance(raw, Mapping) or raw.get("version") not in {
        1,
        2,
        MAGIC_REST_VERSION,
    }:
        raise MagicRestError("Magic rest progress needs staff repair.")
    if raw["version"] in {1, 2} and set(raw) in {expected, v2_expected}:
        raw = {key: value for key, value in raw.items() if key != "last_long_rest"}
        raw["version"] = MAGIC_REST_VERSION
    elif set(raw) != expected:
        raise MagicRestError("Magic rest progress needs staff repair.")
    state = {
        key: raw[key] for key in ("last_sequence", "continuous_pulses", "sleep_pulses")
    }
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in state.values()
        )
        or state["sleep_pulses"] > state["continuous_pulses"]
    ):
        raise MagicRestError("Magic rest progress needs staff repair.")
    return state


def _write_state(owner: Any, state: Mapping[str, int]) -> None:
    """Persist one validated rest-progress snapshot."""
    owner.attributes.add(
        MAGIC_REST_ATTRIBUTE,
        {"version": MAGIC_REST_VERSION, **dict(state)},
    )


def _validate_event(event: PulseEvent) -> None:
    """Accept only a positive token from the shared recovery lane."""
    if (
        not isinstance(event, PulseEvent)
        or event.lane is not PulseLane.RECOVERY
        or isinstance(event.sequence, bool)
        or not isinstance(event.sequence, int)
        or event.sequence < 1
    ):
        raise MagicRestError("Magic rests require a recovery-lane pulse event.")


def _rest_durations() -> tuple[int, int, int]:
    """Read configured MUD-time rest durations with safe relationships."""
    values = (
        getattr(settings, "MAGIC_SHORT_REST_RECOVERY_PULSES", SHORT_REST_PULSES),
        getattr(settings, "MAGIC_LONG_REST_RECOVERY_PULSES", LONG_REST_PULSES),
        getattr(settings, "MAGIC_LONG_REST_SLEEP_PULSES", LONG_REST_SLEEP_PULSES),
    )
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in values
        )
        or values[1] < values[0]
        or values[2] > values[1]
    ):
        raise MagicRestError("Magic rest duration settings are invalid.")
    return values

"""ENV-01's durable calendar and isolated, code-owned boundary consumers.

Time and tokens are persisted before dispatch. As with the central scheduler,
a crash can skip delivery but never replay a boundary and duplicate a reset.
No wall-clock timestamps participate, so cold downtime cannot advance time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia.utils import logger
from systems.pulses import PulseEvent, PulseLane, configured_cadences

CLOCK_ATTRIBUTE = "world_clock"
CLOCK_VERSION = 1
MINUTES_PER_DAY = 1440
MAX_MINUTE = 2_000_000_000 * MINUTES_PER_DAY
_CONSUMERS: dict[str, tuple[frozenset[str], Callable]] = {}


class WorldClockError(ValueError):
    """The clock requires staff repair or explicit settings reconciliation."""


@dataclass(frozen=True)
class ClockBoundary:
    """Stable context for one world-calendar boundary, without live objects."""

    minute: int
    kind: str

    @property
    def day(self) -> int:
        """Return the absolute zero-based calendar day."""
        return self.minute // MINUTES_PER_DAY

    @property
    def hour(self) -> int:
        """Return the boundary hour within its calendar day."""
        return self.minute // 60 % 24

    @property
    def identity(self) -> str:
        """Identify this event consistently across reload and retry."""
        return f"{self.minute}/{self.day}:{self.kind}"


def _integer(value: Any, minimum: int, maximum: int) -> int:
    """Reject booleans and out-of-range primitive clock values."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise WorldClockError("World clock data needs staff repair.")
    return value


def clock_configuration() -> dict[str, int]:
    """Resolve an exact integer step from scheduler cadence and authored scale."""
    epoch = _integer(getattr(settings, "GAME_CLOCK_EPOCH_MINUTE", 720), 0, MAX_MINUTE)
    scale = _integer(
        getattr(settings, "GAME_CLOCK_REAL_SECONDS_PER_HOUR", 600), 1, 86400
    )
    version = _integer(
        getattr(settings, "GAME_CLOCK_SCALE_VERSION", 1), 1, 2_000_000_000
    )
    interval = _integer(getattr(settings, "GAME_PULSE_INTERVAL_SECONDS", 1), 1, 3600)
    numerator = configured_cadences()[PulseLane.WORLD_TIME] * interval * 60
    if numerator % scale or not 1 <= numerator // scale <= MINUTES_PER_DAY:
        raise WorldClockError(
            "World clock scale must produce an integer step of 1–1440 minutes."
        )
    return {
        "epoch": epoch,
        "scale": scale,
        "scale_version": version,
        "step": numerator // scale,
    }


def initial_clock_state() -> dict[str, int]:
    """Start at the configured epoch without inventing elapsed downtime."""
    config = clock_configuration()
    return {
        "version": CLOCK_VERSION,
        "minute": config["epoch"],
        "last_token": 0,
        **config,
    }


def validate_clock_state(raw: Any, *, reconcile: bool = False) -> dict[str, int]:
    """Reject malformed state and settings drift rather than silently resetting it."""
    if not isinstance(raw, Mapping) or set(raw) != set(initial_clock_state()):
        raise WorldClockError("World clock data needs staff repair.")
    state = {key: _integer(value, 0, MAX_MINUTE) for key, value in raw.items()}
    if (
        state["version"] != CLOCK_VERSION
        or state["scale"] < 1
        or state["scale_version"] < 1
        or not 1 <= state["step"] <= MINUTES_PER_DAY
    ):
        raise WorldClockError("World clock data needs staff repair.")
    config = clock_configuration()
    if not reconcile and any(state[key] != value for key, value in config.items()):
        raise WorldClockError("World clock settings need explicit reconciliation.")
    return state


def clock_owner() -> Any:
    """Find the global scheduler; bootstrap queries may precede its creation."""
    from evennia.scripts.models import ScriptDB

    owner = ScriptDB.objects.filter(db_key="game_pulse").first()
    return owner


def clock_state(owner: Any | None = None) -> dict[str, int]:
    """Read a detached validated calendar; never advance time from a query."""
    owner = owner if owner is not None else clock_owner()
    if owner is None:
        return initial_clock_state()
    raw = owner.attributes.get(CLOCK_ATTRIBUTE)
    return initial_clock_state() if raw is None else validate_clock_state(raw)


def reconcile_clock(owner: Any | None = None) -> None:
    """Staff shell boundary: adopt settings while preserving minute and token.

    Epoch changes affect new worlds only; use a new scale version when changing
    cadence or scale. Malformed state must be repaired explicitly in the shell.
    """
    owner = owner if owner is not None else clock_owner()
    if owner is None:
        raise WorldClockError("The world clock is unavailable.")
    with transaction.atomic():
        type(owner).objects.select_for_update().get(pk=owner.pk)
        state = validate_clock_state(
            owner.attributes.get(CLOCK_ATTRIBUTE), reconcile=True
        )
        config = clock_configuration()
        if (
            config != {key: state[key] for key in config}
            and config["scale_version"] <= state["scale_version"]
        ):
            raise WorldClockError(
                "Increase GAME_CLOCK_SCALE_VERSION before reconciliation."
            )
        owner.attributes.add(CLOCK_ATTRIBUTE, {**state, **config})


def register_clock_consumer(
    key: str, kinds: frozenset[str], callback: Callable[[ClockBoundary], None]
) -> None:
    """Register or replace a code-owned adapter, safely across module reloads."""
    if (
        not isinstance(key, str)
        or not key
        or not kinds
        or not kinds <= {"hour", "day", "dawn", "dusk"}
        or not callable(callback)
    ):
        raise WorldClockError("Invalid world clock consumer.")
    _CONSUMERS[key] = (frozenset(kinds), callback)


def advance_world_clock(owner: Any, event: PulseEvent) -> tuple[ClockBoundary, ...]:
    """Consume a fresh token, persist time, then emit each crossed boundary once."""
    if event.lane != PulseLane.WORLD_TIME:
        raise WorldClockError("Invalid world clock lane.")
    token = _integer(event.sequence, 1, MAX_MINUTE)
    with transaction.atomic():
        type(owner).objects.select_for_update().get(pk=owner.pk)
        state = clock_state(owner)
        if token <= state["last_token"]:
            return ()
        old = state["minute"]
        new = _integer(old + state["step"], 0, MAX_MINUTE)
        boundaries = []
        for minute in range((old // 60 + 1) * 60, new + 1, 60):
            boundaries.append(ClockBoundary(minute, "hour"))
            hour = minute // 60 % 24
            kind = {0: "day", 6: "dawn", 18: "dusk"}.get(hour)
            if kind:
                boundaries.append(ClockBoundary(minute, kind))
        owner.attributes.add(
            CLOCK_ATTRIBUTE, {**state, "minute": new, "last_token": token}
        )
    for boundary in boundaries:
        for key, (kinds, callback) in sorted(tuple(_CONSUMERS.items())):
            if boundary.kind in kinds:
                try:
                    callback(boundary)
                except Exception:
                    logger.log_trace(
                        f"World clock consumer {key} failed at {boundary.identity}."
                    )
    return tuple(boundaries)


def is_daylight(minute: int) -> bool:
    """Sunrise is inclusive at 06:00; sunset is exclusive at 18:00."""
    minute = _integer(minute, 0, MAX_MINUTE)
    return 6 <= minute // 60 % 24 < 18


def clock_presentation(minute: int) -> str:
    """Present project-owned dates independently of real dates and time zones."""
    minute = _integer(minute, 0, MAX_MINUTE)
    day, within_day = divmod(minute, MINUTES_PER_DAY)
    year, within_year = divmod(day, 360)
    month, within_month = divmod(within_year, 30)
    hour, minute_of_hour = divmod(within_day, 60)
    band = "daylight" if is_daylight(minute) else "night"
    return f"Year {year + 1}, month {month + 1}, day {within_month + 1}, {hour:02d}:{minute_of_hour:02d} ({band})."


def _mobile_schedule(boundary: ClockBoundary) -> None:
    """Deliver hourly schedule context through MOB-06's isolated special runner."""
    from systems.mobile_specials import (
        MOBILE_SPECIALS_ATTRIBUTE,
        SpecialEvent,
        dispatch_specials,
    )
    from typeclasses.characters import Character

    owners = Character.objects.filter_family(
        db_attributes__db_key=MOBILE_SPECIALS_ATTRIBUTE
    ).distinct()
    for npc in owners.iterator():
        if npc.db.is_player_character is not False:
            continue
        try:
            dispatch_specials(
                npc,
                SpecialEvent(
                    "service",
                    service="schedule",
                    token=boundary.minute,
                    data={
                        "day": boundary.day,
                        "hour": boundary.hour,
                        "identity": boundary.identity,
                    },
                ),
            )
        except Exception:
            logger.log_trace(f"World schedule failed for object #{npc.id}.")


register_clock_consumer("mobiles", frozenset({"hour"}), _mobile_schedule)

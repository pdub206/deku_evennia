"""ENV-02's area-scoped, replay-safe temperate weather service."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia.utils import logger
from evennia.utils.search import search_tag
from systems.areas import AREA_TAG_CATEGORY
from systems.equipment_capabilities import has_equipment_capability
from systems.pulses import PulseEvent, PulseLane
from systems.room_environment import room_environment

WEATHER_ATTRIBUTE = "area_weather"
WEATHER_VERSION = 1
WEATHER_PROFILE_CATEGORY = "weather_profile"
TEMPERATE_PROFILE = "temperate"


class WeatherState(str, Enum):
    """The deliberately small released weather vocabulary."""

    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    STORM = "storm"
    FOG = "fog"


class WeatherError(ValueError):
    """Raised when weather authoring or persisted data is unsafe."""


# Transitions are code-owned; builders cannot author odds or executable logic.
TRANSITIONS = {
    WeatherState.CLEAR: (WeatherState.CLEAR, WeatherState.CLOUDY),
    WeatherState.CLOUDY: (WeatherState.CLEAR, WeatherState.RAIN, WeatherState.FOG),
    WeatherState.RAIN: (WeatherState.CLOUDY, WeatherState.STORM, WeatherState.RAIN),
    WeatherState.STORM: (WeatherState.RAIN, WeatherState.CLOUDY),
    WeatherState.FOG: (WeatherState.CLEAR, WeatherState.CLOUDY, WeatherState.FOG),
}


def _positive_setting(name: str, default: int, maximum: int) -> int:
    value = getattr(settings, name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise WeatherError(f"{name} must be an integer from 1 to {maximum}.")
    return value


def _modifier_setting(name: str, default: float) -> float:
    value = getattr(settings, name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 1 <= float(value) <= 4
    ):
        raise WeatherError(f"{name} must be a number from 1 to 4.")
    return float(value)


def weather_profile(room: Any) -> str | None:
    """Return a room's one valid area profile; malformed identities fail closed."""
    areas = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    profiles = room.tags.get(category=WEATHER_PROFILE_CATEGORY, return_list=True)
    if len(areas) != 1 or len(profiles) != 1 or profiles[0] != TEMPERATE_PROFILE:
        return None
    return profiles[0]


def area_slug(room: Any) -> str | None:
    """Return exactly one safe authored area identity."""
    if weather_profile(room) is None:
        return None
    areas = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    return areas[0] if len(areas) == 1 else None


def set_weather_profile(room: Any, profile: str) -> None:
    """Select ENV-02's sole reviewed profile for every room in one area."""
    if profile != TEMPERATE_PROFILE:
        raise WeatherError("Weather profile must be temperate.")
    areas = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    if len(areas) != 1:
        raise WeatherError("Assign exactly one area before selecting weather.")
    for member in search_tag(areas[0], category=AREA_TAG_CATEGORY):
        for old in member.tags.get(category=WEATHER_PROFILE_CATEGORY, return_list=True):
            member.tags.remove(old, category=WEATHER_PROFILE_CATEGORY)
        member.tags.add(profile, category=WEATHER_PROFILE_CATEGORY)


def _record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "version",
        "state",
        "next_token",
        "last_token",
        "transition_id",
        "profile",
    }:
        raise WeatherError("Weather data needs staff repair.")
    try:
        state = WeatherState(raw["state"])
    except (TypeError, ValueError) as err:
        raise WeatherError("Weather data needs staff repair.") from err
    if raw["version"] != WEATHER_VERSION or raw["profile"] != TEMPERATE_PROFILE:
        raise WeatherError("Weather data needs staff repair.")
    if any(
        isinstance(raw[key], bool) or not isinstance(raw[key], int) or raw[key] < 0
        for key in ("next_token", "last_token")
    ) or not isinstance(raw["transition_id"], str):
        raise WeatherError("Weather data needs staff repair.")
    return {**raw, "state": state.value}


def _initial_record() -> dict[str, Any]:
    return {
        "version": WEATHER_VERSION,
        "state": WeatherState.CLEAR.value,
        "next_token": 1,
        "last_token": 0,
        "transition_id": "",
        "profile": TEMPERATE_PROFILE,
    }


def _records(owner: Any) -> dict[str, dict[str, Any]]:
    raw = owner.attributes.get(WEATHER_ATTRIBUTE)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise WeatherError("Weather data needs staff repair.")
    records = {}
    for slug, record in raw.items():
        if not isinstance(slug, str) or not slug:
            logger.log_warn("Ignoring malformed weather area identity.")
            continue
        try:
            records[slug] = _record(record)
        except WeatherError:
            logger.log_warn(f"Ignoring malformed weather record for area {slug}.")
    return records


def current_state(room: Any, owner: Any | None = None) -> WeatherState | None:
    """Read weather without advancing state; indoors and invalid areas are neutral."""
    try:
        if room_environment(room).indoors:
            return None
        slug = area_slug(room)
        if slug is None:
            return None
        if owner is None:
            from systems.world_clock import clock_owner

            owner = clock_owner()
        if owner is None:
            return WeatherState.CLEAR
        return WeatherState(_records(owner).get(slug, _initial_record())["state"])
    except (AttributeError, WeatherError, ValueError):
        return None


def perception_modifier(room: Any, actor: Any | None = None) -> int:
    """Return the released outdoor penalty, cancelled by worn protection."""
    state = current_state(room)
    if state is None or (
        actor is not None and has_equipment_capability(actor, "weather_protection")
    ):
        return 0
    if state is WeatherState.RAIN:
        return -_positive_setting("GAME_WEATHER_RAIN_PERCEPTION_PENALTY", 2, 20)
    if state in {WeatherState.FOG, WeatherState.STORM}:
        return -_positive_setting("GAME_WEATHER_SEVERE_PERCEPTION_PENALTY", 5, 20)
    return 0


def travel_multiplier(room: Any, actor: Any | None = None) -> float:
    """Return the released outdoor travel multiplier, never below neutral."""
    state = current_state(room)
    if state is None or (
        actor is not None and has_equipment_capability(actor, "weather_protection")
    ):
        return 1.0
    if state is WeatherState.RAIN:
        return _modifier_setting("GAME_WEATHER_RAIN_TRAVEL_MULTIPLIER", 1.25)
    if state is WeatherState.STORM:
        return _modifier_setting("GAME_WEATHER_STORM_TRAVEL_MULTIPLIER", 1.5)
    return 1.0


def blocks_directional_view(room: Any) -> bool:
    """Fog and storms prevent looking through an exit from this outdoor room."""
    return current_state(room) in {WeatherState.FOG, WeatherState.STORM}


def process_weather_pulse(
    owner: Any, event: PulseEvent, *, chooser: Callable = random.choice
) -> None:
    """Transition each valid authored area once, persisting before room notices."""
    if event.lane is not PulseLane.WEATHER:
        raise WeatherError("Invalid weather lane.")
    interval = _positive_setting("GAME_WEATHER_TRANSITION_TOKENS", 1, 10000)
    areas = sorted(
        {
            tag
            for room in search_tag(category=AREA_TAG_CATEGORY)
            if (tag := area_slug(room))
        }
    )
    with transaction.atomic():
        type(owner).objects.select_for_update().get(pk=owner.pk)
        records = _records(owner)
        changed: list[tuple[str, WeatherState]] = []
        for slug in areas:
            record = records.get(slug, _initial_record())
            if event.sequence <= record["last_token"]:
                continue
            record["last_token"] = event.sequence
            if event.sequence >= record["next_token"]:
                old = WeatherState(record["state"])
                try:
                    new = WeatherState(chooser(TRANSITIONS[old]))
                    if new not in TRANSITIONS[old]:
                        raise WeatherError("Illegal weather transition.")
                except Exception as err:
                    logger.log_trace(
                        f"Weather transition failed for area {slug}: {err}"
                    )
                    new = old
                record.update(
                    state=new.value,
                    next_token=event.sequence + interval,
                    transition_id=f"{slug}:{event.sequence}:{new.value}",
                )
                if new is not old:
                    changed.append((slug, new))
            records[slug] = record
        owner.attributes.add(WEATHER_ATTRIBUTE, records)
    for slug, state in changed:
        for room in search_tag(slug, category=AREA_TAG_CATEGORY):
            if current_state(room, owner) is not None:
                room.msg_contents(f"The weather shifts to {state.value}.")

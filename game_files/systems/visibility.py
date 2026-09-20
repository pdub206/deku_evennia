"""Canonical INTERACT-04 visibility and observer-specific discovery decisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from systems.checks import CheckRequest, passive_check, resolve_check
from systems.doors import DoorError, door_state
from systems.room_environment import LightLevel, room_environment

MAX_EXTRA_DESCRIPTIONS = 32
MAX_EXTRA_KEYWORD = 48
MAX_EXTRA_DESCRIPTION = 4000


class Visibility(str, Enum):
    """Player-safe visibility outcomes."""

    VISIBLE = "visible"
    OBSCURED = "obscured"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class VisibilityDecision:
    """A side-effect-free visibility result with a stable safe reason."""

    outcome: Visibility
    reason: str

    @property
    def visible(self) -> bool:
        return self.outcome is Visibility.VISIBLE


def daylight_level(room: Any) -> LightLevel | None:
    """Supply calendar daylight outdoors; indoor rooms ignore the sun."""
    from systems.world_clock import clock_owner, clock_state, is_daylight

    if room_environment(room).indoors:
        return None
    owner = clock_owner()
    if owner is None:
        return None
    return (
        LightLevel.BRIGHT
        if is_daylight(clock_state(owner)["minute"])
        else LightLevel.DARK
    )


def active_light_level(observer: Any, room: Any) -> LightLevel | None:
    """Combine ITEM-05B lamps within their carrier's room."""
    from systems.item_resources import active_light_level as item_light_level

    return item_light_level(observer, room)


def weather_perception_modifier(room: Any, observer: Any | None = None) -> int:
    """Return ENV-02's protected observer-specific outdoor modifier."""
    from systems.weather import perception_modifier

    return perception_modifier(room, observer)


def ambient_light(observer: Any, room: Any) -> LightLevel:
    """Compose authored, daylight, and active-light sources by brightness."""
    environment = room_environment(room)
    authored = environment.light
    levels = [authored]
    if not environment.indoors:
        daylight = daylight_level(room)
        if daylight is not None:
            levels.append(daylight)
    active = active_light_level(observer, room)
    if active is not None:
        levels.append(active)
    rank = {LightLevel.DARK: 0, LightLevel.DIM: 1, LightLevel.BRIGHT: 2}
    return max(levels, key=rank.__getitem__)


def _has_darkvision(observer: Any) -> bool:
    senses = getattr(getattr(observer, "db", None), "senses", ()) or ()
    if isinstance(senses, str):
        senses = (senses,)
    try:
        return any(str(value).casefold() == "darkvision" for value in senses)
    except TypeError:
        return False


def room_visibility(
    observer: Any, room: Any, *, adjacent: bool = False
) -> VisibilityDecision:
    """Decide whether an observer may visually inspect one room."""
    if observer is None or room is None:
        return VisibilityDecision(Visibility.OBSCURED, "invalid_context")
    if not room.access(observer, "view", default=True):
        return VisibilityDecision(Visibility.HIDDEN, "access_denied")
    try:
        level = ambient_light(observer, room)
    except (TypeError, ValueError):
        return VisibilityDecision(Visibility.OBSCURED, "invalid_environment")
    if level is LightLevel.DARK and not (
        not adjacent
        and getattr(observer, "location", None) is room
        and _has_darkvision(observer)
    ):
        return VisibilityDecision(Visibility.OBSCURED, "darkness")
    if weather_perception_modifier(room, observer) < 0:
        return VisibilityDecision(Visibility.OBSCURED, "weather")
    return VisibilityDecision(Visibility.VISIBLE, "clear")


def _discovered(observer: Any, source: Any, target: Any) -> bool:
    record = getattr(getattr(observer, "ndb", None), "visibility_discoveries", None)
    return isinstance(record, set) and (source.id, target.id) in record


def target_visibility(
    observer: Any, target: Any, *, source: Any | None = None
) -> VisibilityDecision:
    """Decide access, ambient visibility, and explicit hidden state for a target."""
    source = source or getattr(observer, "location", None)
    if source is None or getattr(target, "location", None) is not source:
        return VisibilityDecision(Visibility.HIDDEN, "not_local")
    room_result = room_visibility(observer, source)
    if not room_result.visible:
        return room_result
    if not target.access(observer, "view", default=True):
        return VisibilityDecision(Visibility.HIDDEN, "access_denied")
    is_exit = bool(
        getattr(target, "is_typeclass", lambda *_args, **_kwargs: False)(
            "typeclasses.exits.Exit", exact=False
        )
    )
    is_container = str(target.attributes.get("type") or "").casefold() == "container"
    if is_exit or is_container:
        try:
            state = door_state(target)
        except DoorError:
            return VisibilityDecision(Visibility.HIDDEN, "malformed_hidden_state")
    else:
        state = None
    if state is not None and state.hidden and not _discovered(observer, source, target):
        if (
            state.discovery_dc is None
            or passive_perception(observer, source) < state.discovery_dc
        ):
            return VisibilityDecision(Visibility.HIDDEN, "undiscovered")
    return VisibilityDecision(Visibility.VISIBLE, "clear")


def passive_perception(observer: Any, room: Any) -> int:
    """Return canonical passive Perception with environmental input."""
    result = passive_check(
        observer,
        ability="Wisdom",
        skill="Perception",
        action_key="passive_perception",
        override=observer.attributes.get("passive_perception_override"),
    )
    return result.total + weather_perception_modifier(room, observer)


def discover_passively(observer: Any, targets: Sequence[Any]) -> tuple[Any, ...]:
    """Return hidden targets meeting their DC without mutating discovery state."""
    room = getattr(observer, "location", None)
    if room is None or not room_visibility(observer, room).visible:
        return ()
    score = passive_perception(observer, room)
    found = []
    for target in targets:
        try:
            state = door_state(target)
        except DoorError:
            continue
        if (
            state is not None
            and state.hidden
            and state.discovery_dc is not None
            and score >= state.discovery_dc
        ):
            found.append(target)
    return tuple(found)


def active_search(
    observer: Any,
    targets: Sequence[Any],
    *,
    roller: Callable[[int], int],
) -> tuple[Any, ...]:
    """Check each eligible hidden target once and remember only successes."""
    room = getattr(observer, "location", None)
    if room is None or not room_visibility(observer, room).visible:
        return ()
    found = []
    for target in sorted(targets, key=lambda obj: (obj.key.casefold(), obj.id)):
        try:
            state = door_state(target)
        except DoorError:
            continue
        if state is None or not state.hidden or state.discovery_dc is None:
            continue
        check = resolve_check(
            CheckRequest(
                observer,
                "Wisdom",
                max(5, state.discovery_dc),
                skill="Perception",
                action_key="search",
            ),
            roller=roller,
        )
        if (
            check.total + weather_perception_modifier(room, observer)
            >= state.discovery_dc
        ):
            found.append(target)
    if found:
        record = getattr(observer.ndb, "visibility_discoveries", None)
        if not isinstance(record, set):
            record = set()
            observer.ndb.visibility_discoveries = record
        record.update((room.id, target.id) for target in found)
    return tuple(found)


def validate_extra_descriptions(raw: Any) -> list[dict[str, Any]]:
    """Validate bounded ordered descriptive-only keyword records."""
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes))
        or len(raw) > MAX_EXTRA_DESCRIPTIONS
    ):
        raise ValueError("extra descriptions must be a bounded list.")
    cleaned, seen = [], set()
    for entry in raw:
        if not isinstance(entry, Mapping) or set(entry) not in (
            {"keywords", "description"},
            {"keywords", "description", "discovery_dc"},
        ):
            raise ValueError("each extra description needs keywords and description.")
        keywords = entry["keywords"]
        if isinstance(keywords, str):
            keywords = [keywords]
        if not isinstance(keywords, Sequence) or not keywords:
            raise ValueError("extra-description keywords must be a non-empty list.")
        normalized = []
        for keyword in keywords:
            if (
                not isinstance(keyword, str)
                or not keyword.strip()
                or len(keyword.strip()) > MAX_EXTRA_KEYWORD
            ):
                raise ValueError("extra-description keywords are invalid.")
            folded = keyword.strip().casefold()
            if folded in seen:
                raise ValueError("extra-description keywords must be unique.")
            seen.add(folded)
            normalized.append(keyword.strip())
        description = entry["description"]
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > MAX_EXTRA_DESCRIPTION
        ):
            raise ValueError("extra-description text is invalid.")
        dc = entry.get("discovery_dc")
        if dc is not None and (
            isinstance(dc, bool) or not isinstance(dc, int) or not 0 <= dc <= 30
        ):
            raise ValueError(
                "extra-description discovery DC must be 0 through 30 or null."
            )
        cleaned.append(
            {
                "keywords": normalized,
                "description": description.strip(),
                "discovery_dc": dc,
            }
        )
    return cleaned


def matching_extra_descriptions(
    observer: Any, query: str, owners: Sequence[Any]
) -> tuple[tuple[Any, int, dict[str, Any]], ...]:
    """Resolve one exact or unambiguous visible extra-description keyword."""
    folded = query.strip().casefold()
    if not folded:
        return ()
    room = getattr(observer, "location", None)
    passive = passive_perception(observer, room) if room is not None else -1
    exact, abbreviated = [], []
    for owner in owners:
        try:
            records = validate_extra_descriptions(
                owner.attributes.get("extra_descs") or []
            )
        except ValueError:
            continue
        for index, record in enumerate(records):
            dc = record["discovery_dc"]
            discovered = _extra_discovered(observer, room, owner, index)
            if dc is not None and passive < dc and not discovered:
                continue
            keywords = [keyword.casefold() for keyword in record["keywords"]]
            candidate = (owner, index, record)
            if folded in keywords:
                exact.append(candidate)
            elif any(keyword.startswith(folded) for keyword in keywords):
                abbreviated.append(candidate)
    return tuple(exact if exact else abbreviated)


def _extra_discovered(observer: Any, room: Any, owner: Any, index: int) -> bool:
    """Return whether one non-persistent extra-description discovery exists."""
    record = getattr(getattr(observer, "ndb", None), "visibility_discoveries", None)
    return (
        isinstance(record, set)
        and room is not None
        and (room.id, "extra", owner.id, index) in record
    )


def active_search_extras(
    observer: Any,
    owners: Sequence[Any],
    *,
    query: str = "",
    roller: Callable[[int], int],
) -> tuple[tuple[Any, int, dict[str, Any]], ...]:
    """Check every eligible hidden extra description once in stable order."""
    room = getattr(observer, "location", None)
    if room is None or not room_visibility(observer, room).visible:
        return ()
    folded = query.strip().casefold()
    eligible = []
    for owner in owners:
        try:
            records = validate_extra_descriptions(
                owner.attributes.get("extra_descs") or []
            )
        except ValueError:
            continue
        for index, record in enumerate(records):
            dc = record["discovery_dc"]
            keywords = [keyword.casefold() for keyword in record["keywords"]]
            if dc is not None and (
                not folded or any(key.startswith(folded) for key in keywords)
            ):
                eligible.append((owner, index, record))
    found = []
    modifier = weather_perception_modifier(room, observer)
    for owner, index, record in sorted(
        eligible, key=lambda item: (item[0].id, item[1])
    ):
        dc = record["discovery_dc"]
        check = resolve_check(
            CheckRequest(
                observer, "Wisdom", max(5, dc), skill="Perception", action_key="search"
            ),
            roller=roller,
        )
        if check.total + modifier >= dc:
            found.append((owner, index, record))
    if found:
        discoveries = getattr(observer.ndb, "visibility_discoveries", None)
        if not isinstance(discoveries, set):
            discoveries = set()
            observer.ndb.visibility_discoveries = discoveries
        discoveries.update(
            (room.id, "extra", owner.id, index) for owner, index, _ in found
        )
    return tuple(found)

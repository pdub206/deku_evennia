"""Validated room environment, recovery modifiers, and entry hazards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from numbers import Real
from typing import Any

from django.db import transaction
from systems.equipment import DAMAGE_TYPES

ROOM_ENVIRONMENT_ATTRIBUTE = "room_environment"
ROOM_ENVIRONMENT_VERSION = 1
ENTRY_HAZARD_TOKENS_ATTRIBUTE = "entry_hazard_tokens"
ENTRY_HAZARD_TOKENS_VERSION = 1
MIN_RECOVERY_MULTIPLIER = 0.0
MAX_RECOVERY_MULTIPLIER = 3.0
MAX_ENTRY_HAZARD_TOKENS = 100


class RoomEnvironmentError(ValueError):
    """A room environment, hazard, or arrival identity is invalid."""


class LightLevel(str, Enum):
    """Authored ambient light before daylight and carried lights compose."""

    BRIGHT = "bright"
    DIM = "dim"
    DARK = "dark"


class HazardConsequence(str, Enum):
    """The closed set of consequences an entry hazard may own."""

    DAMAGE = "damage"
    EFFECT = "effect"
    DEATH = "death"


@dataclass(frozen=True)
class RoomEnvironment:
    """Canonical environment values read from one room Attribute."""

    indoors: bool = False
    light: LightLevel = LightLevel.BRIGHT
    safe_rest: bool = False
    recovery_multiplier: float = 1.0
    entry_hazard: str | None = None


@dataclass(frozen=True)
class EntryHazard:
    """One registered, declarative entry consequence."""

    key: str
    consequence: HazardConsequence
    amount: int | None = None
    damage_type: str | None = None
    effect_key: str | None = None

    def __post_init__(self) -> None:
        _validate_key(self.key, "hazard")
        if not isinstance(self.consequence, HazardConsequence):
            raise RoomEnvironmentError("A hazard consequence must use its enum.")
        if self.consequence is HazardConsequence.DAMAGE:
            if (
                isinstance(self.amount, bool)
                or not isinstance(self.amount, int)
                or self.amount < 1
                or self.damage_type not in DAMAGE_TYPES
                or self.effect_key is not None
            ):
                raise RoomEnvironmentError("A damage hazard needs fixed typed damage.")
        elif self.consequence is HazardConsequence.EFFECT:
            from systems.effects import EFFECT_REGISTRY

            if (
                not isinstance(self.effect_key, str)
                or EFFECT_REGISTRY.get(self.effect_key) is None
                or self.amount is not None
                or self.damage_type is not None
            ):
                raise RoomEnvironmentError(
                    "An effect hazard needs a registered effect."
                )
        elif any(
            value is not None
            for value in (self.amount, self.damage_type, self.effect_key)
        ):
            raise RoomEnvironmentError(
                "A death hazard accepts no other consequence data."
            )


class EntryHazardRegistry:
    """Code-only registry of validated entry hazards."""

    def __init__(self) -> None:
        self._definitions: dict[str, EntryHazard] = {}

    def register(self, definition: EntryHazard) -> EntryHazard:
        """Register one unique stable hazard definition."""
        if (
            not isinstance(definition, EntryHazard)
            or definition.key in self._definitions
        ):
            raise RoomEnvironmentError(
                "A hazard must be valid and uniquely registered."
            )
        self._definitions[definition.key] = definition
        return definition

    def get(self, key: str) -> EntryHazard | None:
        """Return one exact definition without accepting paths or abbreviations."""
        return self._definitions.get(key)

    def keys(self) -> tuple[str, ...]:
        """Return keys in deterministic registration order."""
        return tuple(self._definitions)


ENTRY_HAZARDS = EntryHazardRegistry()


def default_room_environment() -> dict[str, Any]:
    """Return a new deterministic persistent record with documented defaults."""
    return _environment_data(RoomEnvironment())


def validate_room_environment(raw: Any) -> RoomEnvironment:
    """Validate and normalize the exact data-only environment schema."""
    if raw is None:
        return RoomEnvironment()
    if not isinstance(raw, Mapping):
        raise RoomEnvironmentError("Room environment must be a mapping.")
    allowed = {
        "version",
        "indoors",
        "light",
        "safe_rest",
        "recovery_multiplier",
        "entry_hazard",
    }
    if not set(raw) <= allowed:
        raise RoomEnvironmentError("Room environment contains unsupported fields.")
    version = raw.get("version", ROOM_ENVIRONMENT_VERSION)
    if isinstance(version, bool) or version != ROOM_ENVIRONMENT_VERSION:
        raise RoomEnvironmentError("Room environment has an unsupported version.")
    indoors = raw.get("indoors", False)
    safe_rest = raw.get("safe_rest", False)
    if not isinstance(indoors, bool) or not isinstance(safe_rest, bool):
        raise RoomEnvironmentError("Environment flags must be true or false.")
    try:
        light = LightLevel(raw.get("light", LightLevel.BRIGHT.value))
    except (TypeError, ValueError) as err:
        raise RoomEnvironmentError("Room light must be bright, dim, or dark.") from err
    multiplier = raw.get("recovery_multiplier", 1.0)
    if (
        isinstance(multiplier, bool)
        or not isinstance(multiplier, Real)
        or not MIN_RECOVERY_MULTIPLIER <= float(multiplier) <= MAX_RECOVERY_MULTIPLIER
    ):
        raise RoomEnvironmentError("Recovery multiplier must be between 0 and 3.")
    hazard = raw.get("entry_hazard")
    if hazard is not None:
        _validate_key(hazard, "entry hazard")
        if ENTRY_HAZARDS.get(hazard) is None:
            raise RoomEnvironmentError("Entry hazard is not registered.")
    return RoomEnvironment(indoors, light, safe_rest, float(multiplier), hazard)


def room_environment(room: Any) -> RoomEnvironment:
    """Read the canonical environment without mutating the room."""
    attributes = getattr(room, "attributes", None)
    if attributes is None:
        raise RoomEnvironmentError("Room environment owner is not a room.")
    return validate_room_environment(attributes.get(ROOM_ENVIRONMENT_ATTRIBUTE))


def room_environment_data(room: Any) -> dict[str, Any]:
    """Return normalized primitive data for deterministic area export."""
    return _environment_data(room_environment(room))


def set_room_environment_value(room: Any, field: str, value: Any) -> None:
    """Validate and persist one builder-authored field atomically."""
    if field not in {
        "indoors",
        "light",
        "safe_rest",
        "recovery_multiplier",
        "entry_hazard",
    }:
        raise RoomEnvironmentError("Unknown room-environment field.")
    data = room_environment_data(room)
    data[field] = value.value if isinstance(value, Enum) else value
    room.attributes.add(
        ROOM_ENVIRONMENT_ATTRIBUTE, _environment_data(validate_room_environment(data))
    )


def trigger_entry_hazard(actor: Any, room: Any, arrival_id: str) -> bool:
    """Apply a room's consequence once for one durable committed arrival."""
    _validate_key(arrival_id, "arrival")
    environment = room_environment(room)
    if environment.entry_hazard is None:
        return False
    definition = ENTRY_HAZARDS.get(environment.entry_hazard)
    if definition is None:
        raise RoomEnvironmentError("Entry hazard is not registered.")
    with transaction.atomic():
        tokens = _read_tokens(actor)
        if arrival_id in tokens:
            return False
        # Consume before delegating so retries cannot repeat a consequence.
        tokens.append(arrival_id)
        actor.attributes.add(
            ENTRY_HAZARD_TOKENS_ATTRIBUTE,
            {"version": ENTRY_HAZARD_TOKENS_VERSION, "tokens": tokens[-100:]},
        )
        if definition.consequence is HazardConsequence.DAMAGE:
            from systems.injury import apply_damage

            apply_damage(
                actor,
                definition.amount or 0,
                source_kind=f"environment:{definition.damage_type}",
            )
        elif definition.consequence is HazardConsequence.EFFECT:
            actor.effects.add(
                definition.effect_key or "", source_key=definition.key, quiet=False
            )
        else:
            from systems.injury import apply_terminal_death

            apply_terminal_death(actor, source_kind=f"environment:{definition.key}")
    return True


def _environment_data(environment: RoomEnvironment) -> dict[str, Any]:
    """Serialize enums while retaining a closed, stable field order."""
    data = asdict(environment)
    data["light"] = environment.light.value
    return {"version": ROOM_ENVIRONMENT_VERSION, **data}


def _read_tokens(actor: Any) -> list[str]:
    """Validate durable arrival bookkeeping before a hazard can execute."""
    raw = actor.attributes.get(ENTRY_HAZARD_TOKENS_ATTRIBUTE)
    if raw is None:
        return []
    if (
        not isinstance(raw, Mapping)
        or raw.get("version") != ENTRY_HAZARD_TOKENS_VERSION
        or set(raw) != {"version", "tokens"}
        or isinstance(raw["tokens"], (str, bytes))
        or not isinstance(raw["tokens"], Sequence)
        or len(raw["tokens"]) > MAX_ENTRY_HAZARD_TOKENS
    ):
        raise RoomEnvironmentError("Entry hazard tokens need staff repair.")
    for token in raw["tokens"]:
        _validate_key(token, "stored arrival")
    if len(set(raw["tokens"])) != len(raw["tokens"]):
        raise RoomEnvironmentError("Entry hazard tokens need staff repair.")
    return list(raw["tokens"])


def _validate_key(value: Any, label: str) -> None:
    """Accept bounded lowercase registry and identity keys only."""
    allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 100
        or value != value.strip().lower()
        or any(char not in allowed for char in value)
    ):
        raise RoomEnvironmentError(f"A {label} key is invalid.")

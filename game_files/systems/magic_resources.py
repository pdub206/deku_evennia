"""Durable MAGIC-02 class resources with derived ADV-02 maxima.

Only this module reads or changes mutable non-HP magic resources.  The current
value is persisted as a small primitive map while the maximum remains derived
from the actor's current ADV-02 class and level.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from systems.progression import CLASS_PROGRESSION

MAGIC_RESOURCE_ATTRIBUTE = "magic_resources"
MAGIC_RESOURCE_VERSION = 1


class MagicResourceError(ValueError):
    """A magic resource is unavailable, malformed, or cannot be spent."""


def resource_maximum(actor: Any, resource_key: str) -> int:
    """Return an actor's derived maximum for HP or one active class resource."""
    if resource_key == "hp":
        return actor.stats.hp_max
    resource = CLASS_PROGRESSION.resources.get(resource_key)
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    if (
        resource is None
        or not isinstance(class_key, str)
        or not isinstance(level, int)
        or isinstance(level, bool)
        or not 1 <= level <= 20
        or not resource_key.startswith(f"{class_key.casefold()}.")
    ):
        raise MagicResourceError("Magic resource is unavailable.")
    return resource.maxima[level - 1]


def resource_current(actor: Any, resource_key: str) -> int:
    """Return the current value, initializing a newly unlocked resource full."""
    maximum = resource_maximum(actor, resource_key)
    if resource_key == "hp":
        return actor.stats.hp_current
    state = _state(actor)
    current = state.get(resource_key, maximum)
    if current > maximum:
        current = maximum
        state[resource_key] = current
        _write_state(actor, state)
    return current


def spend_resource(actor: Any, resource_key: str, amount: int) -> int:
    """Atomically spend a bounded amount and return the remaining resource."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise MagicResourceError("Magic resource cost is invalid.")
    current = resource_current(actor, resource_key)
    if amount > current:
        raise MagicResourceError("Magic resource is insufficient.")
    if resource_key == "hp":
        actor.stats.take_damage(amount)
        return actor.stats.hp_current
    state = _state(actor)
    state[resource_key] = current - amount
    _write_state(actor, state)
    return state[resource_key]


def restore_resource(actor: Any, resource_key: str, amount: int) -> int:
    """Restore up to the derived maximum for recovery adapters and refunds."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise MagicResourceError("Magic resource recovery is invalid.")
    maximum = resource_maximum(actor, resource_key)
    current = resource_current(actor, resource_key)
    if resource_key == "hp":
        return actor.stats.heal(amount)
    state = _state(actor)
    state[resource_key] = min(maximum, current + amount)
    _write_state(actor, state)
    return state[resource_key]


def resource_view(actor: Any) -> tuple[tuple[str, int, int], ...]:
    """Return active class-resource keys with current and derived maxima."""
    class_key = actor.attributes.get("char_class")
    if not isinstance(class_key, str):
        return ()
    prefix = f"{class_key.casefold()}."
    keys = tuple(key for key in CLASS_PROGRESSION.resources if key.startswith(prefix))
    return tuple(
        (key, resource_current(actor, key), resource_maximum(actor, key))
        for key in keys
    )


def _state(actor: Any) -> dict[str, int]:
    """Read a detached primitive value map, failing closed for corrupt state."""
    raw = actor.attributes.get(MAGIC_RESOURCE_ATTRIBUTE)
    if raw is None:
        return {}
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "current"}
        or raw["version"] != MAGIC_RESOURCE_VERSION
        or not isinstance(raw["current"], Mapping)
    ):
        raise MagicResourceError("Magic resource state needs staff repair.")
    values: dict[str, int] = {}
    for key, value in raw["current"].items():
        if (
            not isinstance(key, str)
            or key not in CLASS_PROGRESSION.resources
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise MagicResourceError("Magic resource state needs staff repair.")
        values[key] = value
    return values


def _write_state(actor: Any, current: Mapping[str, int]) -> None:
    """Persist a detached resource map after its public boundary validated it."""
    actor.attributes.add(
        MAGIC_RESOURCE_ATTRIBUTE,
        {"version": MAGIC_RESOURCE_VERSION, "current": dict(current)},
    )

"""Durable MAGIC-02 class resources with derived ADV-02 maxima.

Only this module reads or changes mutable non-HP magic resources.  The current
value is persisted as a small primitive map while the maximum remains derived
from the actor's current ADV-02 class and level.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from systems.progression import CLASS_PROGRESSION

MAGIC_RESOURCE_ATTRIBUTE = "magic_resources"
MAGIC_RESOURCE_VERSION = 1
_SLOT_RESOURCE_PREFIX = "spell_slot."
_PACT_SLOT_RESOURCE = "pact_slot"
_RECOVERY_PROFILES = frozenset({"short_rest", "long_rest"})


class MagicResourceError(ValueError):
    """A magic resource is unavailable, malformed, or cannot be spent."""


@dataclass(frozen=True)
class SpellSlotOption:
    """One currently legal slot choice for a declared leveled spell."""

    resource_key: str
    slot_level: int
    current: int
    maximum: int


def resource_maximum(actor: Any, resource_key: str) -> int:
    """Return an actor's derived maximum for HP, class resource, or slot."""
    if resource_key == "hp":
        return actor.stats.hp_max
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    if (
        not isinstance(class_key, str)
        or not isinstance(level, int)
        or isinstance(level, bool)
        or not 1 <= level <= 20
    ):
        raise MagicResourceError("Magic resource is unavailable.")
    slot_maximum = _slot_maximum(class_key, level, resource_key)
    if slot_maximum is not None:
        if slot_maximum < 1:
            raise MagicResourceError("Magic resource is unavailable.")
        return slot_maximum
    resource = CLASS_PROGRESSION.resources.get(resource_key)
    if resource is None or not resource_key.startswith(f"{class_key.casefold()}."):
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
    """Return active class-resource and currently usable slot keys."""
    class_key = actor.attributes.get("char_class")
    if not isinstance(class_key, str):
        return ()
    prefix = f"{class_key.casefold()}."
    keys = (
        *(key for key in CLASS_PROGRESSION.resources if key.startswith(prefix)),
        *_slot_resource_keys(class_key, actor.attributes.get("level", 1)),
    )
    return tuple(
        (key, resource_current(actor, key), resource_maximum(actor, key))
        for key in keys
    )


def spell_slot_options(actor: Any, spell_level: int) -> tuple[SpellSlotOption, ...]:
    """Return legal slot choices for one spell level without spending one.

    A cantrip has no slot option. Ordinary casters may choose any available
    slot at or above the spell's level, while Pact Magic exposes exactly its
    single shared slot level.  The return value includes current capacity so a
    command can present legal upcasts without duplicating class-table logic.
    """
    if (
        isinstance(spell_level, bool)
        or not isinstance(spell_level, int)
        or not 0 <= spell_level <= 9
    ):
        raise MagicResourceError("Spell level is invalid.")
    if spell_level == 0:
        return ()
    class_key, level = _class_and_level(actor)
    access = _spell_access(class_key)
    if access is None:
        raise MagicResourceError("Magic resource is unavailable.")
    prefix = f"{class_key.casefold()}."
    if access.pact_slots[level - 1]:
        pact_level = access.pact_slot_level[level - 1]
        if pact_level < spell_level:
            return ()
        key = f"{prefix}{_PACT_SLOT_RESOURCE}"
        return (
            SpellSlotOption(
                key,
                pact_level,
                resource_current(actor, key),
                resource_maximum(actor, key),
            ),
        )
    options: list[SpellSlotOption] = []
    for slot_level in range(spell_level, len(access.spell_slots) + 1):
        key = f"{prefix}{_SLOT_RESOURCE_PREFIX}{slot_level}"
        maximum = _slot_maximum(class_key, level, key)
        if maximum:
            options.append(
                SpellSlotOption(key, slot_level, resource_current(actor, key), maximum)
            )
    return tuple(options)


def recover_profile(actor: Any, profile: str) -> tuple[tuple[str, int], ...]:
    """Restore resources eligible for one completed rest-recovery profile.

    The caller must establish and validate the actual uninterrupted rest. This
    narrow operation deliberately has no timer or player command of its own.
    A completed long rest restores both long-rest resources and resources that
    normally recover on a short rest.
    """
    if profile not in _RECOVERY_PROFILES:
        raise MagicResourceError("Magic resource recovery profile is invalid.")
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= 20
    ):
        raise MagicResourceError("Magic resource is unavailable.")
    allowed_profiles = {profile}
    if profile == "long_rest":
        allowed_profiles.add("short_rest")
    prefix = f"{class_key.casefold()}."
    keys = [
        resource.key
        for resource in CLASS_PROGRESSION.resources.values()
        if resource.key.startswith(prefix)
        and resource.recovery_profile in allowed_profiles
    ]
    if profile == "long_rest":
        access = _spell_access(class_key)
        if access is not None:
            keys.extend(
                f"{class_key.casefold()}.{_SLOT_RESOURCE_PREFIX}{spell_level}"
                for spell_level, maxima in enumerate(access.spell_slots, start=1)
                if maxima[level - 1] > 0
            )
    if profile in allowed_profiles:
        pact_key = f"{class_key.casefold()}.{_PACT_SLOT_RESOURCE}"
        if _slot_maximum(class_key, level, pact_key):
            keys.append(pact_key)
    restored: list[tuple[str, int]] = []
    for key in keys:
        maximum = resource_maximum(actor, key)
        current = resource_current(actor, key)
        if current < maximum:
            restored.append((key, restore_resource(actor, key, maximum - current)))
    return tuple(restored)


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
            or not _known_resource_key(key)
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


def _spell_access(class_key: str):
    """Return one class's spell-access table without exposing a raw mapping."""
    return CLASS_PROGRESSION.spell_access.get(f"{class_key.casefold()}.spell_access")


def _class_and_level(actor: Any) -> tuple[str, int]:
    """Read the effective class row once for slot-option resolution."""
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= 20
    ):
        raise MagicResourceError("Magic resource is unavailable.")
    return class_key, level


def _slot_maximum(class_key: str, level: int, resource_key: str) -> int | None:
    """Resolve one namespaced ordinary or Pact Magic slot resource."""
    prefix = f"{class_key.casefold()}."
    if not resource_key.startswith(prefix):
        return None
    access = _spell_access(class_key)
    if access is None:
        return None
    suffix = resource_key[len(prefix) :]
    if suffix == _PACT_SLOT_RESOURCE:
        return access.pact_slots[level - 1]
    if not suffix.startswith(_SLOT_RESOURCE_PREFIX):
        return None
    try:
        spell_level = int(suffix.removeprefix(_SLOT_RESOURCE_PREFIX))
    except ValueError:
        return None
    if not 1 <= spell_level <= len(access.spell_slots):
        return None
    return access.spell_slots[spell_level - 1][level - 1]


def _slot_resource_keys(class_key: str, level: Any) -> tuple[str, ...]:
    """Return only slot keys whose current class table grants nonzero capacity."""
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 20:
        return ()
    access = _spell_access(class_key)
    if access is None:
        return ()
    prefix = f"{class_key.casefold()}."
    ordinary = tuple(
        f"{prefix}{_SLOT_RESOURCE_PREFIX}{spell_level}"
        for spell_level, maxima in enumerate(access.spell_slots, start=1)
        if maxima[level - 1] > 0
    )
    pact = (
        (f"{prefix}{_PACT_SLOT_RESOURCE}",) if access.pact_slots[level - 1] > 0 else ()
    )
    return (*ordinary, *pact)


def _known_resource_key(key: str) -> bool:
    """Accept only declared class resources or structurally valid slot keys."""
    if key in CLASS_PROGRESSION.resources:
        return True
    for class_key in CLASS_PROGRESSION.definitions:
        for level in range(1, 21):
            if _slot_maximum(class_key, level, key) is not None:
                return True
    return False

"""ITEM-08A's bounded, validated equipment-stat modifier registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evennia import logger
from world.chargen_data import ABILITY_NAMES, SKILLS

EQUIPMENT_MODIFIERS_ATTRIBUTE = "equipment_modifiers"
COMBINE_EXTREMES = "extremes"
COMBINE_CAPPED_SUM = "capped_sum"
_WEARABLE_TYPES = frozenset({"armor", "worn", "light", "other", "treasure"})
_HANDHELD_TYPES = frozenset({"weapon", "wand", "staff"})
_BODY_SLOTS = frozenset(
    {
        "right finger",
        "left finger",
        "neck",
        "back",
        "body",
        "head",
        "legs",
        "feet",
        "hands",
        "arms",
        "about",
        "waist",
        "right wrist",
        "left wrist",
        "right shoulder",
        "left shoulder",
        "right ankle",
        "left ankle",
        "on belt",
    }
)


class EquipmentModifierError(ValueError):
    """Raised when an equipment modifier profile is not safe to author."""


@dataclass(frozen=True)
class EquipmentModifierDefinition:
    """One explicitly released numeric equipment modifier."""

    name: str
    item_types: frozenset[str]
    wear_locations: frozenset[str]
    minimum: int
    maximum: int
    combination: str


class EquipmentModifierRegistry:
    """Resolve the finite ITEM-08A modifier vocabulary."""

    def __init__(self, definitions: tuple[EquipmentModifierDefinition, ...]):
        self.definitions = {definition.name: definition for definition in definitions}
        if len(self.definitions) != len(definitions):
            raise EquipmentModifierError("Equipment modifier names must be unique.")

    def definition_for(self, name: str) -> EquipmentModifierDefinition:
        try:
            return self.definitions[name]
        except KeyError as err:
            raise EquipmentModifierError(
                f"Unknown equipment modifier: {name!r}"
            ) from err


def _definitions() -> tuple[EquipmentModifierDefinition, ...]:
    """Build released definitions from canonical ability and skill names."""
    entries: list[EquipmentModifierDefinition] = []
    for ability in ABILITY_NAMES:
        key = ability.lower()
        entries.extend(
            (
                EquipmentModifierDefinition(
                    f"ability:{key}",
                    _WEARABLE_TYPES,
                    _BODY_SLOTS,
                    -5,
                    5,
                    COMBINE_EXTREMES,
                ),
                EquipmentModifierDefinition(
                    f"saving_throw:{key}",
                    _WEARABLE_TYPES,
                    _BODY_SLOTS,
                    -5,
                    5,
                    COMBINE_EXTREMES,
                ),
            )
        )
    entries.extend(
        EquipmentModifierDefinition(
            f"skill:{skill.lower()}",
            _WEARABLE_TYPES,
            _BODY_SLOTS,
            -5,
            5,
            COMBINE_EXTREMES,
        )
        for skill in SKILLS
    )
    entries.extend(
        (
            EquipmentModifierDefinition(
                "passive_perception",
                _WEARABLE_TYPES,
                _BODY_SLOTS,
                -5,
                5,
                COMBINE_EXTREMES,
            ),
            EquipmentModifierDefinition(
                "speed",
                _WEARABLE_TYPES | _HANDHELD_TYPES,
                _BODY_SLOTS | {"wield", "hold"},
                -30,
                30,
                COMBINE_CAPPED_SUM,
            ),
            EquipmentModifierDefinition(
                "carry_capacity",
                _WEARABLE_TYPES,
                _BODY_SLOTS,
                -30,
                30,
                COMBINE_CAPPED_SUM,
            ),
        )
    )
    return tuple(entries)


EQUIPMENT_MODIFIER_REGISTRY = EquipmentModifierRegistry(_definitions())


def validate_equipment_modifiers(
    raw: Any, item_type: Any, wear_locations: Any
) -> dict[str, int]:
    """Validate a complete authored mapping; malformed mappings fail closed."""
    if not isinstance(raw, Mapping):
        raise EquipmentModifierError("Equipment modifiers must be a mapping.")
    if not isinstance(item_type, str):
        raise EquipmentModifierError("An equipment modifier item needs a valid type.")
    if isinstance(wear_locations, str) or not isinstance(wear_locations, Sequence):
        raise EquipmentModifierError(
            "Equipment modifier wear locations must be a list."
        )
    item_type = item_type.strip().lower()
    slots = {str(slot).strip().lower() for slot in wear_locations}
    validated: dict[str, int] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            raise EquipmentModifierError("Equipment modifier names must be strings.")
        definition = EQUIPMENT_MODIFIER_REGISTRY.definition_for(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise EquipmentModifierError(
                f"Equipment modifier '{name}' must be an integer."
            )
        if not definition.minimum <= value <= definition.maximum:
            raise EquipmentModifierError(
                f"Equipment modifier '{name}' must be between {definition.minimum} and {definition.maximum}."
            )
        if item_type not in definition.item_types:
            raise EquipmentModifierError(
                f"Equipment modifier '{name}' is not allowed on {item_type!r} items."
            )
        if not slots or not slots <= definition.wear_locations:
            raise EquipmentModifierError(
                f"Equipment modifier '{name}' is not allowed in this item's wear slot."
            )
        validated[name] = value
    return validated


def item_equipment_modifiers(item: Any) -> dict[str, int]:
    """Return an item's valid profile, logging and quarantining invalid data."""
    raw = item.db.equipment_modifiers
    if raw is None and item.db.stat_modifiers is not None:
        _diagnose(
            item, "legacy stat_modifiers are no longer an equipment modifier profile"
        )
        return {}
    if raw is None:
        return {}
    try:
        return validate_equipment_modifiers(raw, item.db.type, item.db.wear_locations)
    except EquipmentModifierError as err:
        _diagnose(item, str(err))
        return {}


def _diagnose(item: Any, detail: str) -> None:
    """Emit a staff-visible diagnostic without exposing malformed data to players."""
    message = (
        f"Ignoring invalid equipment modifiers on #{getattr(item, 'id', '?')}: {detail}"
    )
    item.ndb.equipment_modifier_diagnostic = message
    logger.log_warn(message)


def combine_equipment_modifiers(
    sources: tuple[Mapping[str, int], ...],
) -> dict[str, int]:
    """Apply the released highest/worst and capped-additive combination rules."""
    values: dict[str, list[int]] = {}
    for source in sources:
        for name, value in source.items():
            values.setdefault(name, []).append(value)
    combined: dict[str, int] = {}
    for name, contributions in values.items():
        definition = EQUIPMENT_MODIFIER_REGISTRY.definition_for(name)
        if definition.combination == COMBINE_EXTREMES:
            combined[name] = max((v for v in contributions if v > 0), default=0) + min(
                (v for v in contributions if v < 0), default=0
            )
        else:
            combined[name] = max(
                definition.minimum, min(definition.maximum, sum(contributions))
            )
    return combined

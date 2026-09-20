"""ITEM-08B's finite, validated utility-equipment capability registry.

Capabilities are categorical: a character either has a released capability or
does not.  This service deliberately does not infer training, spells, or
effects from an item; consumers combine those independent sources explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from evennia import logger
from systems.equipment import DAMAGE_TYPES, WEAR_LOCATIONS

EQUIPMENT_CAPABILITIES_ATTRIBUTE = "equipment_capabilities"
CARRIED = "carried"
EQUIPPED = "equipped"


class EquipmentCapabilityError(ValueError):
    """Raised when a utility capability profile is unsafe to author."""


@dataclass(frozen=True)
class EquipmentCapabilityDefinition:
    """One reviewed categorical capability and how an item may supply it."""

    name: str
    item_types: frozenset[str]
    requirement: str
    wear_locations: frozenset[str] = frozenset()


class EquipmentCapabilityRegistry:
    """Resolve only the finite ITEM-08B capability vocabulary."""

    def __init__(self, definitions: tuple[EquipmentCapabilityDefinition, ...]):
        self.definitions = {definition.name: definition for definition in definitions}
        if len(self.definitions) != len(definitions):
            raise EquipmentCapabilityError("Equipment capability names must be unique.")

    def definition_for(self, name: str) -> EquipmentCapabilityDefinition:
        try:
            return self.definitions[name]
        except KeyError as err:
            raise EquipmentCapabilityError(
                f"Unknown equipment capability: {name!r}"
            ) from err


_BODY_SLOTS = frozenset({"body", "back", "head", "about", "feet", "hands"})
_EQUIPPED_WEARABLES = frozenset({"armor", "worn", "other", "treasure"})
EQUIPMENT_CAPABILITY_REGISTRY = EquipmentCapabilityRegistry(
    (
        EquipmentCapabilityDefinition("terrain:boat", frozenset({"boat"}), CARRIED),
        EquipmentCapabilityDefinition(
            "terrain:swim", _EQUIPPED_WEARABLES, EQUIPPED, _BODY_SLOTS
        ),
        EquipmentCapabilityDefinition(
            "terrain:flight", _EQUIPPED_WEARABLES, EQUIPPED, _BODY_SLOTS
        ),
        EquipmentCapabilityDefinition("light", frozenset({"light"}), CARRIED),
        EquipmentCapabilityDefinition(
            "tool:thieves_tools", frozenset({"other"}), CARRIED
        ),
        EquipmentCapabilityDefinition(
            "resistance:fire", _EQUIPPED_WEARABLES, EQUIPPED, _BODY_SLOTS
        ),
        EquipmentCapabilityDefinition(
            "weather_protection", _EQUIPPED_WEARABLES, EQUIPPED, _BODY_SLOTS
        ),
        # A bounded bridge to the reviewed ITEM-04B/05B wand/staff definitions.
        EquipmentCapabilityDefinition(
            "activation:charged", frozenset({"wand", "staff"}), CARRIED
        ),
    )
)


def validate_equipment_capabilities(
    raw: Any, item_type: Any, wear_locations: Any
) -> list[str]:
    """Validate an authored list; malformed capability data fails closed."""
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise EquipmentCapabilityError("Equipment capabilities must be a list.")
    if not isinstance(item_type, str):
        raise EquipmentCapabilityError(
            "An equipment capability item needs a valid type."
        )
    if wear_locations is None:
        wear_locations = []
    if isinstance(wear_locations, str) or not isinstance(wear_locations, Sequence):
        raise EquipmentCapabilityError(
            "Equipment capability wear locations must be a list."
        )
    item_type = item_type.strip().lower()
    slots = {str(slot).strip().lower() for slot in wear_locations}
    validated: list[str] = []
    for name in raw:
        if not isinstance(name, str):
            raise EquipmentCapabilityError(
                "Equipment capability names must be strings."
            )
        definition = EQUIPMENT_CAPABILITY_REGISTRY.definition_for(name)
        if item_type not in definition.item_types:
            raise EquipmentCapabilityError(
                f"Equipment capability '{name}' is not allowed on {item_type!r} items."
            )
        if definition.requirement == EQUIPPED and (
            not slots or not slots <= definition.wear_locations
        ):
            raise EquipmentCapabilityError(
                f"Equipment capability '{name}' is not allowed in this item's wear slot."
            )
        if name in validated:
            raise EquipmentCapabilityError(
                "Equipment capabilities cannot be duplicated."
            )
        validated.append(name)
    return validated


def item_equipment_capabilities(item: Any) -> frozenset[str]:
    """Return one valid item's capabilities, quarantining malformed profiles."""
    raw = item.db.equipment_capabilities
    if raw is None:
        return frozenset()
    try:
        return frozenset(
            validate_equipment_capabilities(raw, item.db.type, item.db.wear_locations)
        )
    except EquipmentCapabilityError as err:
        message = f"Ignoring invalid equipment capabilities on #{getattr(item, 'id', '?')}: {err}"
        item.ndb.equipment_capability_diagnostic = message
        logger.log_warn(message)
        return frozenset()


def has_equipment_capability(actor: Any, name: str) -> bool:
    """Return whether a directly carried/equipped item supplies ``name``."""
    try:
        definition = EQUIPMENT_CAPABILITY_REGISTRY.definition_for(name)
    except EquipmentCapabilityError:
        return False
    for item in getattr(actor, "contents", ()):
        if name not in item_equipment_capabilities(item):
            continue
        if definition.requirement == CARRIED:
            return True
        if str(item.db.worn_location or "").strip().lower() in WEAR_LOCATIONS:
            return True
    return False


def has_damage_resistance(actor: Any, damage_type: str) -> bool:
    """Return whether equipped equipment grants one released resistance."""
    normalized = str(damage_type).strip().lower()
    if normalized not in DAMAGE_TYPES:
        return False
    return has_equipment_capability(actor, f"resistance:{normalized}")

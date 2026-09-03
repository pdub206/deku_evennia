"""Canonical equipment state, training, and locational protection rules."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any

from world.chargen_data import CLASSES

WEAR_LOCATIONS: tuple[str, ...] = (
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
    "shield",
    "about",
    "waist",
    "right wrist",
    "left wrist",
    "wield",
    "hold",
    "right shoulder",
    "left shoulder",
    "right ankle",
    "left ankle",
    # TODO: Implement affixing items to a worn belt.
    "on belt",
)

WEAR_SIDES: tuple[str, str] = ("left", "right")

HIT_LOCATIONS: tuple[str, ...] = (
    "head",
    "neck",
    "body",
    "right shoulder",
    "left shoulder",
    "right arm",
    "left arm",
    "right wrist",
    "left wrist",
    "right hand",
    "left hand",
    "right leg",
    "left leg",
    "right foot",
    "left foot",
)

WEAR_LOCATION_HIT_LOCATIONS: dict[str, tuple[str, ...]] = {
    "head": ("head",),
    "neck": ("neck",),
    "body": ("body",),
    "right shoulder": ("right shoulder",),
    "left shoulder": ("left shoulder",),
    "arms": ("right arm", "left arm"),
    "right wrist": ("right wrist",),
    "left wrist": ("left wrist",),
    "hands": ("right hand", "left hand"),
    "legs": ("right leg", "left leg"),
    "feet": ("right foot", "left foot"),
}

ARMOR_CATEGORIES: tuple[str, ...] = ("light", "medium", "heavy", "shield")
WEAPON_CATEGORIES: tuple[str, ...] = ("simple", "martial")
ATTACK_ABILITIES: tuple[str, ...] = ("strength", "dexterity")
PHYSICAL_DAMAGE_TYPES: tuple[str, ...] = (
    "bludgeoning",
    "piercing",
    "slashing",
)
DAMAGE_TYPES: tuple[str, ...] = (
    "acid",
    *PHYSICAL_DAMAGE_TYPES,
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "poison",
    "psychic",
    "radiant",
    "thunder",
)
MAX_MITIGATION_PERCENT = 80


class EquipmentError(ValueError):
    """Raised when an equipment-state transition violates slot invariants."""


@dataclass(frozen=True)
class DamageMitigation:
    """Describe how equipped armor changed one instance of incoming damage."""

    incoming: int
    final: int
    percentage: int
    flat: int

    @property
    def prevented(self) -> int:
        """Return the amount of damage prevented after all reductions."""
        return self.incoming - self.final


def wear_phrase(location: str) -> str:
    """Return a natural phrase describing where an item is equipped."""
    if location == "wield":
        return "as your weapon"
    if location == "hold":
        return "in your offhand"
    if location == "shield":
        return "as your shield"
    if location == "about":
        return "about your body"
    if location == "on belt":
        return "on your belt"
    return f"on your {location}"


def allowed_wear_locations(item: Any) -> tuple[str, ...]:
    """Return an item's valid, normalized equipment locations."""
    raw_locations = item.db.wear_locations or []
    if isinstance(raw_locations, str):
        raw_locations = [raw_locations]
    return tuple(
        dict.fromkeys(
            str(location).lower()
            for location in raw_locations
            if str(location).lower() in WEAR_LOCATIONS
        )
    )


def clear_equipped_state(item: Any) -> None:
    """Remove an item's equipped marker without mutating character statistics."""
    item.db.worn_location = None


def _identifier(value: Any) -> str:
    """Normalize a builder-facing weapon identity for proficiency matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


class EquipmentHandler:
    """Read and change one character's equipment through a single rules API."""

    def __init__(self, owner: Any):
        self.owner = owner

    @property
    def equipped_items(self) -> tuple[Any, ...]:
        """Return carried items occupying recognized equipment slots."""
        return tuple(
            item
            for item in self.owner.contents
            if str(item.db.worn_location or "").lower() in WEAR_LOCATIONS
        )

    def item_at(self, location: str) -> Any | None:
        """Return the item occupying the requested location, if any."""
        normalized = str(location).strip().lower()
        return next(
            (
                item
                for item in self.equipped_items
                if str(item.db.worn_location).lower() == normalized
            ),
            None,
        )

    def equip(self, item: Any, location: str) -> None:
        """Equip a carried item after enforcing ownership and slot invariants."""
        normalized = str(location).strip().lower()
        if item.location is not self.owner:
            raise EquipmentError("The item must be carried before it can be equipped.")
        if normalized not in allowed_wear_locations(item):
            raise EquipmentError("The item cannot be equipped in that location.")
        occupying_item = self.item_at(normalized)
        if occupying_item is not None and occupying_item is not item:
            raise EquipmentError("That equipment location is already occupied.")
        item.db.worn_location = normalized

    def unequip(self, item: Any) -> None:
        """Clear a carried item's equipped state."""
        if item.location is not self.owner:
            raise EquipmentError("Only a carried item can be unequipped.")
        clear_equipped_state(item)

    def unequip_all(self) -> tuple[Any, ...]:
        """Unequip every item and return the items whose state changed."""
        equipped = self.equipped_items
        for item in equipped:
            clear_equipped_state(item)
        return equipped

    @staticmethod
    def _item_type(item: Any) -> str:
        return str(item.db.type or "").strip().lower()

    @staticmethod
    def _armor_category(item: Any) -> str:
        return str(item.db.subtype or "").strip().lower()

    @property
    def primary_armor(self) -> Any | None:
        """Return valid light, medium, or heavy armor worn on the body."""
        item = self.item_at("body")
        if (
            item is not None
            and self._item_type(item) == "armor"
            and self._armor_category(item) in ARMOR_CATEGORIES[:-1]
        ):
            return item
        return None

    @property
    def shield(self) -> Any | None:
        """Return a shield-category armor item equipped in the shield slot."""
        item = self.item_at("shield")
        if (
            item is not None
            and self._item_type(item) == "armor"
            and self._armor_category(item) == "shield"
        ):
            return item
        return None

    @property
    def wielded_weapon(self) -> Any | None:
        """Return the weapon item occupying the wield slot."""
        item = self.item_at("wield")
        if item is not None and self._item_type(item) == "weapon":
            return item
        return None

    def _class_data(self) -> Mapping[str, Any]:
        class_name = self.owner.attributes.get("char_class", default="Fighter")
        return CLASSES.get(class_name, CLASSES["Fighter"])

    def is_weapon_proficient(self, item: Any) -> bool:
        """Return whether the owner is trained with a particular weapon."""
        if self._item_type(item) != "weapon":
            return False
        class_data = self._class_data()
        categories = {
            _identifier(value) for value in class_data.get("weapon_categories", [])
        }
        weapons = {
            _identifier(value) for value in class_data.get("weapon_proficiencies", [])
        }
        category = _identifier(item.db.weapon_category)
        weapon_kind = _identifier(item.db.weapon_kind)
        return bool(
            (category and category in categories)
            or (weapon_kind and weapon_kind in weapons)
        )

    def is_armor_proficient(self, item: Any) -> bool:
        """Return whether the owner is trained with an armor item's category."""
        if self._item_type(item) != "armor":
            return False
        category = self._armor_category(item)
        if category not in ARMOR_CATEGORIES:
            return False
        trained = {
            str(value).strip().lower().removesuffix("s")
            for value in self._class_data().get("armor_training", [])
        }
        return category in trained

    @property
    def has_untrained_armor(self) -> bool:
        """Return whether any equipped armor is outside class training."""
        return any(
            self._item_type(item) == "armor"
            and self._armor_category(item) in ARMOR_CATEGORIES
            and not self.is_armor_proficient(item)
            for item in self.equipped_items
        )

    def stat_modifier_sources(self) -> tuple[Mapping[str, Real], ...]:
        """Return non-AC modifier mappings supplied by equipped items."""
        sources: list[Mapping[str, Real]] = []
        for item in self.equipped_items:
            modifiers = item.db.stat_modifiers
            if not isinstance(modifiers, Mapping):
                continue
            # Base AC, shields, and locational mitigation are deliberately typed
            # rules. Generic equipment cannot create an unlimited additive AC stack.
            filtered = {
                name: value
                for name, value in modifiers.items()
                if name != "armor_class" and isinstance(value, Real)
            }
            if filtered:
                sources.append(filtered)
        return tuple(sources)

    def mitigate_damage(
        self, amount: int, hit_location: str, damage_type: str
    ) -> DamageMitigation:
        """Apply armor at one hit location to a non-negative damage amount."""
        if amount < 0:
            raise ValueError("Damage cannot be negative.")
        location = str(hit_location).strip().lower()
        if location not in HIT_LOCATIONS:
            raise ValueError(f"Unknown hit location: {hit_location}")
        normalized_damage_type = str(damage_type).strip().lower()
        if normalized_damage_type not in DAMAGE_TYPES:
            raise ValueError(f"Unknown damage type: {damage_type}")

        applicable_items = []
        for item in self.equipped_items:
            worn_location = str(item.db.worn_location).lower()
            if location not in WEAR_LOCATION_HIT_LOCATIONS.get(worn_location, ()):
                continue
            if self._item_type(item) != "armor":
                continue
            configured_types = item.db.mitigation_types
            if not configured_types:
                configured_types = PHYSICAL_DAMAGE_TYPES
            if isinstance(configured_types, str):
                configured_types = [configured_types]
            if normalized_damage_type in {
                str(value).strip().lower() for value in configured_types
            }:
                applicable_items.append(item)

        percentage = min(
            MAX_MITIGATION_PERCENT,
            sum(
                max(0, int(item.db.mitigation_percent or 0))
                for item in applicable_items
            ),
        )
        flat = sum(
            max(0, int(item.db.mitigation_flat or 0)) for item in applicable_items
        )
        percentage_reduction = math.floor(amount * percentage / 100)
        final = max(0, amount - percentage_reduction - flat)
        return DamageMitigation(
            incoming=amount,
            final=final,
            percentage=percentage,
            flat=flat,
        )

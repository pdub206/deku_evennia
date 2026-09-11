"""Narrow adapters for released class-choice mechanics.

Progression owns declarative choices; this module owns only mechanics that the
current alpha rules and equipment model can represent faithfully.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

IMPLEMENTED_FEATURE_OPTIONS = frozenset({"Protector", "Defense"})


def validate_feature_option(option: str) -> None:
    """Reject a declared option whose owning mechanic is not yet available."""
    if option not in IMPLEMENTED_FEATURE_OPTIONS:
        raise ValueError(
            "That class option is not mechanically available in the alpha."
        )


def has_feature_option(character: Any, option: str) -> bool:
    """Return whether a character has durably selected an implemented option."""
    return option in (character.attributes.get("class_feature_choices") or ())


def equipment_training(character: Any) -> tuple[frozenset[str], frozenset[str]]:
    """Return extra armor categories and weapon categories from class choices."""
    if has_feature_option(character, "Protector"):
        return frozenset({"heavy"}), frozenset({"martial"})
    return frozenset(), frozenset()


def stat_modifier_sources(character: Any) -> tuple[Mapping[str, int], ...]:
    """Return conditional numeric modifiers supplied by selected choices."""
    armor = character.equipment.primary_armor
    if (
        has_feature_option(character, "Defense")
        and armor is not None
        and str(armor.db.subtype).strip().casefold() in {"light", "medium", "heavy"}
    ):
        return ({"armor_class": 1},)
    return ()

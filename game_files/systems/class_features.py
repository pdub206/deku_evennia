"""Narrow adapters for released class-choice mechanics.

Progression owns declarative choices; this module owns only mechanics that the
current alpha rules and equipment model can represent faithfully.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def has_granted_feature(character: Any, feature_key: str) -> bool:
    """Read one automatic ADV-01 feature grant without inferring from level."""
    raw = character.attributes.get("class_progression")
    grants = raw.get("grants") if isinstance(raw, Mapping) else None
    return (
        isinstance(feature_key, str)
        and isinstance(grants, Sequence)
        and not isinstance(grants, (str, bytes))
        and feature_key in grants
    )


def spell_healing_bonus(character: Any, spell_level: int) -> int:
    """Return Life Domain's bounded Disciple of Life bonus for a slot spell."""
    if spell_level > 0 and has_granted_feature(character, "cleric.disciple_of_life"):
        return 2 + spell_level
    return 0


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

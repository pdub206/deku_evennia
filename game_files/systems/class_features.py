"""Narrow adapters for released class-choice mechanics.

Progression owns declarative choices; this module owns only mechanics that the
current alpha rules and equipment model can represent faithfully.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

IMPLEMENTED_FEATURE_OPTIONS = frozenset({"Protector", "Defense"})
WEAPON_MASTERY_PROPERTIES = {
    "handaxe": "vex",
    "longsword": "sap",
    "mace": "sap",
    "rapier": "vex",
    "shortbow": "vex",
    "shortsword": "vex",
    "spear": "sap",
}


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


def weapon_mastery_for_attack(character: Any, weapon: Any) -> str | None:
    """Return the selected Sap or Vex property for the wielded weapon kind."""
    if weapon is None or not any(
        has_granted_feature(character, key)
        for key in ("fighter.weapon_mastery", "rogue.weapon_mastery")
    ):
        return None
    from systems.equipment import _identifier

    weapon_kind = _identifier(weapon.attributes.get("weapon_kind"))
    selected = character.attributes.get("weapon_masteries") or ()
    if weapon_kind not in selected:
        return None
    return WEAPON_MASTERY_PROPERTIES.get(weapon_kind)


def climbing_speed(character: Any) -> int:
    """Return the character's released climbing speed in feet per move."""
    speed = character.stats.speed
    if has_granted_feature(character, "rogue.second_story_work"):
        return speed
    return speed // 2


def jump_distance(character: Any, *, high_jump: bool = False) -> int:
    """Return running jump distance using the feature's Dexterity adaptation."""
    ability = (
        "Dexterity"
        if has_granted_feature(character, "rogue.second_story_work")
        else "Strength"
    )
    score = character.stats.ability_score(ability)
    if high_jump:
        return max(0, 3 + character.stats.ability_modifier(ability))
    return max(0, score)


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

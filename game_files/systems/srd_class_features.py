"""SRD 5.2.1 class-feature table data, separate from released mechanics.

These are factual feature names and level grants from the local SRD source.
They deliberately do not register player-selectable actions or claim that a
feature is implemented.  MAGIC-03 adapters promote an entry only after its
mechanics, help, resource, and ownership requirements are complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from systems.progression import MAX_CLASS_LEVEL, SELECTABLE_CLASS_NAMES


@dataclass(frozen=True)
class SRDClassFeatureTable:
    """One cited, immutable sequence of non-subclass class-feature names."""

    class_key: str
    level_features: tuple[tuple[str, ...], ...]
    srd_reference: str

    def features_at(self, level: int) -> tuple[str, ...]:
        """Return the exact feature names introduced at one class level."""
        if not 1 <= level <= MAX_CLASS_LEVEL:
            raise KeyError(f"Class level must be 1 through {MAX_CLASS_LEVEL}.")
        return self.level_features[level - 1]


def _table(
    class_key: str, page: int, levels: tuple[tuple[str, ...], ...]
) -> SRDClassFeatureTable:
    """Build a table while enforcing the local SRD table's full level range."""
    if len(levels) != MAX_CLASS_LEVEL:
        raise ValueError(f"{class_key} needs {MAX_CLASS_LEVEL} feature rows.")
    return SRDClassFeatureTable(
        class_key,
        levels,
        f"SRD 5.2.1 p.{page}: {class_key} Features table",
    )


SRD_CLASS_FEATURES: Mapping[str, SRDClassFeatureTable] = MappingProxyType(
    {
        "Barbarian": _table(
            "Barbarian",
            28,
            (
                ("Rage", "Unarmored Defense", "Weapon Mastery"),
                ("Danger Sense", "Reckless Attack"),
                ("Barbarian Subclass", "Primal Knowledge"),
                ("Ability Score Improvement",),
                ("Extra Attack", "Fast Movement"),
                ("Subclass Feature",),
                ("Feral Instinct", "Instinctive Pounce"),
                ("Ability Score Improvement",),
                ("Brutal Strike",),
                ("Subclass Feature",),
                ("Relentless Rage",),
                ("Ability Score Improvement",),
                ("Improved Brutal Strike",),
                ("Subclass Feature",),
                ("Persistent Rage",),
                ("Ability Score Improvement",),
                ("Improved Brutal Strike",),
                ("Indomitable Might",),
                ("Epic Boon",),
                ("Primal Champion",),
            ),
        ),
        "Bard": _table(
            "Bard",
            31,
            (
                ("Bardic Inspiration", "Spellcasting"),
                ("Expertise", "Jack of All Trades"),
                ("Bard Subclass",),
                ("Ability Score Improvement",),
                ("Font of Inspiration",),
                ("Subclass Feature",),
                ("Countercharm",),
                ("Ability Score Improvement",),
                ("Expertise",),
                ("Magical Secrets",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Superior Inspiration",),
                ("Epic Boon",),
                ("Words of Creation",),
            ),
        ),
        "Cleric": _table(
            "Cleric",
            36,
            (
                ("Spellcasting", "Divine Order"),
                ("Channel Divinity",),
                ("Cleric Subclass",),
                ("Ability Score Improvement",),
                ("Sear Undead",),
                ("Subclass Feature",),
                ("Blessed Strikes",),
                ("Ability Score Improvement",),
                (),
                ("Divine Intervention",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Improved Blessed Strikes",),
                (),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                (),
                ("Epic Boon",),
                ("Greater Divine Intervention",),
            ),
        ),
        "Druid": _table(
            "Druid",
            41,
            (
                ("Spellcasting", "Druidic", "Primal Order"),
                ("Wild Shape", "Wild Companion"),
                ("Druid Subclass",),
                ("Ability Score Improvement",),
                ("Wild Resurgence",),
                ("Subclass Feature",),
                ("Elemental Fury",),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                ("Improved Elemental Fury",),
                ("Ability Score Improvement",),
                (),
                ("Beast Spells",),
                ("Epic Boon",),
                ("Archdruid",),
            ),
        ),
        "Fighter": _table(
            "Fighter",
            47,
            (
                ("Fighting Style", "Second Wind", "Weapon Mastery"),
                ("Action Surge", "Tactical Mind"),
                ("Fighter Subclass",),
                ("Ability Score Improvement",),
                ("Extra Attack", "Tactical Shift"),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Indomitable", "Tactical Master"),
                ("Subclass Feature",),
                ("Two Extra Attacks",),
                ("Ability Score Improvement",),
                ("Indomitable", "Studied Attacks"),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Action Surge", "Indomitable"),
                ("Subclass Feature",),
                ("Epic Boon",),
                ("Three Extra Attacks",),
            ),
        ),
        "Monk": _table(
            "Monk",
            49,
            (
                ("Martial Arts", "Unarmored Defense"),
                ("Monk's Focus", "Unarmored Movement", "Uncanny Metabolism"),
                ("Deflect Attacks", "Monk Subclass"),
                ("Ability Score Improvement", "Slow Fall"),
                ("Extra Attack", "Stunning Strike"),
                ("Empowered Strikes", "Subclass Feature"),
                ("Evasion",),
                ("Ability Score Improvement",),
                ("Acrobatic Movement",),
                ("Heightened Focus", "Self-Restoration"),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Deflect Energy",),
                ("Disciplined Survivor",),
                ("Perfect Focus",),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Superior Defense",),
                ("Epic Boon",),
                ("Body and Mind",),
            ),
        ),
        "Paladin": _table(
            "Paladin",
            53,
            (
                ("Lay On Hands", "Spellcasting", "Weapon Mastery"),
                ("Fighting Style", "Paladin's Smite"),
                ("Channel Divinity", "Paladin Subclass"),
                ("Ability Score Improvement",),
                ("Extra Attack", "Faithful Steed"),
                ("Aura of Protection",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Abjure Foes",),
                ("Aura of Courage",),
                ("Radiant Strikes",),
                ("Ability Score Improvement",),
                (),
                ("Restoring Touch",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                (),
                ("Aura Expansion",),
                ("Epic Boon",),
                ("Subclass Feature",),
            ),
        ),
        "Ranger": _table(
            "Ranger",
            57,
            (
                ("Spellcasting", "Favored Enemy", "Weapon Mastery"),
                ("Deft Explorer", "Fighting Style"),
                ("Ranger Subclass",),
                ("Ability Score Improvement",),
                ("Extra Attack",),
                ("Roving",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Expertise",),
                ("Tireless",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Relentless Hunter",),
                ("Nature's Veil",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Precise Hunter",),
                ("Feral Senses",),
                ("Epic Boon",),
                ("Foe Slayer",),
            ),
        ),
        "Rogue": _table(
            "Rogue",
            61,
            (
                ("Expertise", "Sneak Attack", "Thieves' Cant", "Weapon Mastery"),
                ("Cunning Action",),
                ("Rogue Subclass", "Steady Aim"),
                ("Ability Score Improvement",),
                ("Cunning Strike", "Uncanny Dodge"),
                ("Expertise",),
                ("Evasion", "Reliable Talent"),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Ability Score Improvement",),
                ("Improved Cunning Strike",),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Devious Strikes",),
                ("Slippery Mind",),
                ("Ability Score Improvement",),
                ("Subclass Feature",),
                ("Elusive",),
                ("Epic Boon",),
                ("Stroke of Luck",),
            ),
        ),
        "Sorcerer": _table(
            "Sorcerer",
            64,
            (
                ("Spellcasting", "Innate Sorcery"),
                ("Font of Magic", "Metamagic"),
                ("Sorcerer Subclass",),
                ("Ability Score Improvement",),
                ("Sorcerous Restoration",),
                ("Subclass Feature",),
                ("Sorcery Incarnate",),
                ("Ability Score Improvement",),
                (),
                ("Metamagic",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                ("Metamagic",),
                ("Subclass Feature",),
                ("Epic Boon",),
                ("Arcane Apotheosis",),
            ),
        ),
        "Warlock": _table(
            "Warlock",
            70,
            (
                ("Eldritch Invocations", "Pact Magic"),
                ("Magical Cunning",),
                ("Warlock Subclass",),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                ("Contact Patron",),
                ("Subclass Feature",),
                ("Mystic Arcanum",),
                ("Ability Score Improvement",),
                ("Mystic Arcanum",),
                ("Subclass Feature",),
                ("Mystic Arcanum",),
                ("Ability Score Improvement",),
                ("Mystic Arcanum",),
                (),
                ("Epic Boon",),
                ("Eldritch Master",),
            ),
        ),
        "Wizard": _table(
            "Wizard",
            77,
            (
                ("Spellcasting", "Ritual Adept", "Arcane Recovery"),
                ("Scholar",),
                ("Wizard Subclass",),
                ("Ability Score Improvement",),
                ("Memorize Spell",),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Subclass Feature",),
                (),
                ("Ability Score Improvement",),
                (),
                ("Spell Mastery",),
                ("Epic Boon",),
                ("Signature Spells",),
            ),
        ),
    }
)


if tuple(SRD_CLASS_FEATURES) != SELECTABLE_CLASS_NAMES:
    raise RuntimeError("SRD class-feature tables must cover every selectable class.")

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


@dataclass(frozen=True)
class SRDSubclassFeatureTable:
    """One cited SRD subclass and the features it introduces by class level."""

    class_key: str
    subclass_name: str
    level_features: tuple[tuple[str, ...], ...]
    srd_reference: str

    def features_at(self, level: int) -> tuple[str, ...]:
        """Return this subclass's exact feature names at one class level."""
        if not 1 <= level <= MAX_CLASS_LEVEL:
            raise KeyError(f"Class level must be 1 through {MAX_CLASS_LEVEL}.")
        return self.level_features[level - 1]


@dataclass(frozen=True)
class SRDFeatureClassification:
    """The reviewed release shape and future system owner for one feature."""

    feature_shape: str
    release_adapter: str


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


def _subclass_table(
    class_key: str,
    page: int,
    subclass_name: str,
    features: Mapping[int, tuple[str, ...]],
) -> SRDSubclassFeatureTable:
    """Build a complete subclass table from its sparse feature-level mapping."""
    if any(not 1 <= level <= MAX_CLASS_LEVEL for level in features):
        raise ValueError(f"{subclass_name} has an invalid class level.")
    return SRDSubclassFeatureTable(
        class_key,
        subclass_name,
        tuple(features.get(level, ()) for level in range(1, MAX_CLASS_LEVEL + 1)),
        f"SRD 5.2.1 p.{page}: {class_key} Subclass: {subclass_name}",
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


# SRD 5.2.1 supplies one representative subclass for each base class. These
# tables record only names and source levels; progression keeps them catalogued
# until their own selection and mechanics adapters are implemented.
SRD_SUBCLASS_FEATURES: Mapping[str, SRDSubclassFeatureTable] = MappingProxyType(
    {
        "Barbarian": _subclass_table(
            "Barbarian",
            30,
            "Path of the Berserker",
            {
                3: ("Frenzy",),
                6: ("Mindless Rage",),
                10: ("Retaliation",),
                14: ("Intimidating Presence",),
            },
        ),
        "Bard": _subclass_table(
            "Bard",
            35,
            "College of Lore",
            {
                3: ("Bonus Proficiencies", "Cutting Words"),
                6: ("Magical Discoveries",),
                14: ("Peerless Skill",),
            },
        ),
        "Cleric": _subclass_table(
            "Cleric",
            40,
            "Life Domain",
            {
                3: ("Disciple of Life", "Preserve Life"),
                6: ("Blessed Healer",),
                17: ("Supreme Healing",),
            },
        ),
        "Druid": _subclass_table(
            "Druid",
            46,
            "Circle of the Land",
            {
                3: ("Circle of the Land Spells", "Land's Aid"),
                6: ("Natural Recovery",),
                10: ("Nature's Ward",),
                14: ("Nature's Sanctuary",),
            },
        ),
        "Fighter": _subclass_table(
            "Fighter",
            49,
            "Champion",
            {
                3: ("Improved Critical", "Remarkable Athlete"),
                7: ("Additional Fighting Style",),
                10: ("Heroic Warrior",),
                15: ("Superior Critical",),
                18: ("Survivor",),
            },
        ),
        "Monk": _subclass_table(
            "Monk",
            52,
            "Warrior of the Open Hand",
            {
                3: ("Open Hand Technique",),
                6: ("Wholeness of Body",),
                11: ("Fleet Step",),
                17: ("Quivering Palm",),
            },
        ),
        "Paladin": _subclass_table(
            "Paladin",
            56,
            "Oath of Devotion",
            {
                3: ("Oath of Devotion Spells", "Sacred Weapon"),
                7: ("Aura of Devotion",),
                15: ("Smite of Protection",),
                20: ("Holy Nimbus",),
            },
        ),
        "Ranger": _subclass_table(
            "Ranger",
            61,
            "Hunter",
            {
                3: ("Hunter's Lore", "Hunter's Prey"),
                7: ("Defensive Tactics",),
                11: ("Superior Hunter's Prey",),
                15: ("Superior Hunter's Defense",),
            },
        ),
        "Rogue": _subclass_table(
            "Rogue",
            64,
            "Thief",
            {
                3: ("Fast Hands", "Second-Story Work"),
                9: ("Supreme Sneak",),
                13: ("Use Magic Device",),
                17: ("Thief's Reflexes",),
            },
        ),
        "Sorcerer": _subclass_table(
            "Sorcerer",
            69,
            "Draconic Sorcery",
            {
                3: ("Draconic Resilience", "Draconic Spells"),
                6: ("Elemental Affinity",),
                14: ("Dragon Wings",),
                18: ("Dragon Companion",),
            },
        ),
        "Warlock": _subclass_table(
            "Warlock",
            76,
            "Fiend Patron",
            {
                3: ("Dark One's Blessing", "Fiend Spells"),
                6: ("Dark One's Own Luck",),
                10: ("Fiendish Resilience",),
                14: ("Hurl Through Hell",),
            },
        ),
        "Wizard": _subclass_table(
            "Wizard",
            82,
            "Evoker",
            {
                3: ("Potent Cantrip",),
                6: ("Sculpt Spells",),
                10: ("Empowered Evocation",),
                14: ("Overchannel",),
            },
        ),
    }
)


if tuple(SRD_CLASS_FEATURES) != SELECTABLE_CLASS_NAMES:
    raise RuntimeError("SRD class-feature tables must cover every selectable class.")
if tuple(SRD_SUBCLASS_FEATURES) != SELECTABLE_CLASS_NAMES:
    raise RuntimeError("SRD subclass tables must cover every selectable class.")


# These are feature identities whose SRD text requires the player to make a
# selection. The actual options remain catalogued until their narrow owning
# adapter is implemented.
_CHOICE_FEATURES = frozenset(
    {
        "Ability Score Improvement",
        "Additional Fighting Style",
        "Bard Subclass",
        "Barbarian Subclass",
        "Bonus Proficiencies",
        "Cleric Subclass",
        "Circle of the Land Spells",
        "Divine Order",
        "Draconic Spells",
        "Druid Subclass",
        "Eldritch Invocations",
        "Epic Boon",
        "Expertise",
        "Fighter Subclass",
        "Fighting Style",
        "Fiend Spells",
        "Hunter's Lore",
        "Hunter's Prey",
        "Magical Secrets",
        "Metamagic",
        "Monk Subclass",
        "Mystic Arcanum",
        "Oath of Devotion Spells",
        "Paladin Subclass",
        "Primal Knowledge",
        "Primal Order",
        "Ranger Subclass",
        "Rogue Subclass",
        "Sorcerer Subclass",
        "Warlock Subclass",
        "Weapon Mastery",
        "Wizard Subclass",
    }
)
_RESOURCE_FEATURES = frozenset(
    {
        "Bardic Inspiration",
        "Channel Divinity",
        "Font of Inspiration",
        "Font of Magic",
        "Lay On Hands",
        "Monk's Focus",
        "Pact Magic",
        "Rage",
        "Uncanny Metabolism",
        "Wild Resurgence",
        "Wild Shape",
    }
)
_MAGIC_FEATURES = frozenset(
    {
        "Arcane Apotheosis",
        "Arcane Recovery",
        "Archdruid",
        "Beast Spells",
        "Divine Intervention",
        "Greater Divine Intervention",
        "Magical Cunning",
        "Memorize Spell",
        "Ritual Adept",
        "Spell Mastery",
        "Spellcasting",
        "Words of Creation",
    }
)
_ACTIVE_FEATURES = frozenset(
    {
        "Abjure Foes",
        "Action Surge",
        "Brutal Strike",
        "Cunning Action",
        "Cunning Strike",
        "Deflect Attacks",
        "Deflect Energy",
        "Divine Smite",
        "Frenzy",
        "Intimidating Presence",
        "Open Hand Technique",
        "Paladin's Smite",
        "Reckless Attack",
        "Second Wind",
        "Stunning Strike",
        "Tactical Shift",
    }
)


def classify_srd_feature(feature_name: str) -> SRDFeatureClassification:
    """Return the reviewed non-executable ownership classification for a name.

    This deliberately assigns an adapter category rather than a callable. A
    catalogue entry cannot become released until code provides that adapter.
    Unlisted feature names are automatic passive grants owned by ADV-02's
    passive-stat bridge; their detailed modifier implementation remains a
    separate release requirement.
    """
    if feature_name in _CHOICE_FEATURES:
        return SRDFeatureClassification("choice", "advancement.choice")
    if feature_name in _RESOURCE_FEATURES:
        return SRDFeatureClassification("resource", "resources.class_feature")
    if feature_name in _MAGIC_FEATURES:
        return SRDFeatureClassification("active", "magic.class_feature")
    if feature_name in _ACTIVE_FEATURES:
        return SRDFeatureClassification("active", "combat.class_feature")
    if feature_name == "Subclass Feature":
        return SRDFeatureClassification("passive", "subclass.feature")
    return SRDFeatureClassification("passive", "advancement.passive")

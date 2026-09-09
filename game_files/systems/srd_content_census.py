"""Frozen, source-controlled P04-A01 census of currently authored SRD content.

The census is deliberately a catalogue rather than a release switch.  It
turns every source occurrence already represented in DEKU into one immutable
record with a precise citation, a single future owning package, and a stable
key.  P-04 consumers can therefore distinguish an absent source row from a
catalogued-but-unimplemented mechanic without treating either as released.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from systems.progression import CLASS_PROGRESSION, MAX_CLASS_LEVEL
from systems.srd_class_resources import SRD_CLASS_RESOURCES
from systems.srd_spell_lists import (SRD_CANTRIP_LISTS,
                                     SRD_LEVEL_EIGHT_SPELL_LISTS,
                                     SRD_LEVEL_FIVE_SPELL_LISTS,
                                     SRD_LEVEL_FOUR_SPELL_LISTS,
                                     SRD_LEVEL_NINE_SPELL_LISTS,
                                     SRD_LEVEL_ONE_SPELL_LISTS,
                                     SRD_LEVEL_SEVEN_SPELL_LISTS,
                                     SRD_LEVEL_SIX_SPELL_LISTS,
                                     SRD_LEVEL_THREE_SPELL_LISTS,
                                     SRD_LEVEL_TWO_SPELL_LISTS,
                                     SRDSpellListEntry)
from world.chargen_data import SPECIES

# Version 2 corrects the source boundary: the pinned SRD describes four
# backgrounds, not all sixteen compatibility backgrounds still present in the
# chargen menu. It also records Barding, which is a separate equipment rule in
# the Mounts and Vehicles section rather than an armor-table row.
CONTENT_CENSUS_VERSION = 2
_P04_TASKS = frozenset(
    {
        "P04-A04",
        "P04-A05",
        "P04-A06",
        "P04-A07",
        "P04-A08",
        "P04-A09",
        "P04-A10",
        "P04-O01",
        "P04-S00",
        "P04-S01",
        "P04-S02",
        "P04-S03",
        "P04-S04",
        "P04-S05",
        "P04-S06",
        "P04-S07",
        "P04-S08",
        "P04-S09",
    }
)


class ContentCensusError(ValueError):
    """Raised when authored source content has no single accountable owner."""


@dataclass(frozen=True)
class CensusRecord:
    """One immutable SRD occurrence and its single implementation owner."""

    key: str
    kind: str
    display_name: str
    class_key: str | None
    level: int | None
    srd_reference: str
    owner_task: str
    adapter: str
    release_state: str
    adaptation: str = ""


@dataclass(frozen=True)
class ContentCensus:
    """Validated source inventory used for release and backlog diagnostics."""

    version: int
    records: tuple[CensusRecord, ...]
    fingerprint: str

    def by_key(self, key: str) -> CensusRecord:
        """Return one stable record or fail without guessing an owner."""
        try:
            return MappingProxyType({record.key: record for record in self.records})[
                key
            ]
        except KeyError as err:
            raise ContentCensusError(f"Unknown census record: {key}") from err


def build_content_census(
    records: Iterable[CensusRecord], *, version: int = CONTENT_CENSUS_VERSION
) -> ContentCensus:
    """Validate and freeze the complete authored source-record inventory."""
    if version != CONTENT_CENSUS_VERSION:
        raise ContentCensusError("Content census has an unsupported version.")
    frozen = tuple(records)
    keys = [record.key for record in frozen]
    if len(keys) != len(set(keys)):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise ContentCensusError(
            "Content census contains duplicate record keys: " + ", ".join(duplicates)
        )
    for record in frozen:
        _validate_record(record)
    _validate_feature_projection(frozen)
    payload = {"version": version, "records": [asdict(record) for record in frozen]}
    return ContentCensus(
        version,
        frozen,
        sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    )


def census_report(census: ContentCensus = None) -> Mapping[str, tuple[str, ...]]:
    """Return deterministic catalogue/release status without changing state."""
    census = SRD_CONTENT_CENSUS if census is None else census
    catalogued = tuple(
        record.key for record in census.records if record.release_state == "catalogued"
    )
    released = tuple(
        record.key for record in census.records if record.release_state == "released"
    )
    return MappingProxyType({"catalogued": catalogued, "released": released})


def _validate_record(record: CensusRecord) -> None:
    if (
        not isinstance(record.key, str)
        or not record.key
        or record.kind
        not in {
            "base_feature",
            "subclass_choice",
            "subclass_feature",
            "resource",
            "spell_access",
            "spell",
            "feat",
            "origin_grant",
            "creature_reference",
            "equipment_reference",
            "background",
            "species",
        }
        or not isinstance(record.display_name, str)
        or not record.display_name
        or record.owner_task not in _P04_TASKS
        or not isinstance(record.adapter, str)
        or not record.adapter
        or record.release_state not in {"catalogued", "released"}
        or not isinstance(record.srd_reference, str)
        or not record.srd_reference.startswith("SRD 5.2.1 ")
    ):
        raise ContentCensusError(
            f"Census record '{getattr(record, 'key', '?')}' is invalid."
        )
    minimum_level = 0 if record.kind == "spell" else 1
    if record.level is not None and (
        isinstance(record.level, bool)
        or not minimum_level <= record.level <= MAX_CLASS_LEVEL
    ):
        raise ContentCensusError(f"Census record '{record.key}' has an invalid level.")


def _validate_feature_projection(records: tuple[CensusRecord, ...]) -> None:
    """Require one census occurrence for every progression-table source row."""
    record_keys = {record.key for record in records}
    expected: set[str] = set()
    for definition in CLASS_PROGRESSION.definitions.values():
        for grants in definition.levels:
            expected.update(
                _occurrence_key(key, grants.level)
                for key in (
                    *grants.automatic_feature_keys,
                    *grants.catalogued_feature_keys,
                    *grants.catalogued_subclass_choice_keys,
                    *grants.catalogued_subclass_feature_keys,
                )
            )
            expected.update(f"spell_access:{key}" for key in grants.spell_access_keys)
            expected.update(f"resource:{key}" for key in grants.resource_keys)
    missing = expected - record_keys
    if missing:
        raise ContentCensusError(
            "Content census is missing progression occurrences: "
            + ", ".join(sorted(missing))
        )


def _task_for_adapter(adapter: str) -> str:
    """Map each reviewed future adapter to its sole frozen P-04 package."""
    if adapter == "advancement.choice":
        return "P04-A04"
    if adapter == "resources.class_feature":
        return "P04-A06"
    if adapter == "magic.class_feature":
        return "P04-A10"
    if adapter == "subclass.feature":
        return "P04-A04"
    if adapter == "character_stats.unarmored_defense":
        return "P04-A05"
    return "P04-A05"


def _default_records() -> tuple[CensusRecord, ...]:
    """Project existing source-controlled class/origin data into P04-A01."""
    records: list[CensusRecord] = []
    attached_spell_access: set[str] = set()
    for definition in CLASS_PROGRESSION.definitions.values():
        for level in range(1, MAX_CLASS_LEVEL + 1):
            grants = definition.grants_at(level)
            for feature_key in (
                *grants.automatic_feature_keys,
                *grants.catalogued_feature_keys,
            ):
                feature = CLASS_PROGRESSION.features[feature_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "base_feature",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        _task_for_adapter(feature.release_adapter),
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for choice_key in grants.catalogued_subclass_choice_keys:
                feature = CLASS_PROGRESSION.features[choice_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "subclass_choice",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        "P04-A04",
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for feature_key in grants.catalogued_subclass_feature_keys:
                feature = CLASS_PROGRESSION.features[feature_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "subclass_feature",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        _task_for_adapter(feature.release_adapter),
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for resource_key in grants.resource_keys:
                resource = CLASS_PROGRESSION.resources[resource_key]
                records.append(
                    CensusRecord(
                        f"resource:{resource.key}",
                        "resource",
                        resource.display_name,
                        definition.key,
                        level,
                        resource.srd_reference,
                        "P04-A06",
                        resource.owner,
                        resource.release_state,
                    )
                )
            for access_key in grants.spell_access_keys:
                if access_key in attached_spell_access:
                    continue
                attached_spell_access.add(access_key)
                access = CLASS_PROGRESSION.spell_access[access_key]
                records.append(
                    CensusRecord(
                        f"spell_access:{access.key}",
                        "spell_access",
                        access.key,
                        definition.key,
                        level,
                        access.srd_reference,
                        "P04-A09",
                        "magic.spell_access",
                        "catalogued",
                    )
                )
    # Source records not yet attached to a released level remain visible too.
    attached_resources = {
        record.key.removeprefix("resource:")
        for record in records
        if record.kind == "resource"
    }
    for resource in SRD_CLASS_RESOURCES.values():
        if resource.key not in attached_resources:
            records.append(
                CensusRecord(
                    f"resource:{resource.key}",
                    "resource",
                    resource.display_name,
                    resource.key.split(".")[0].title(),
                    None,
                    resource.srd_reference,
                    "P04-A06",
                    "resources.class_feature",
                    "catalogued",
                )
            )
    for name in _PINNED_SRD_BACKGROUNDS:
        records.append(
            CensusRecord(
                f"background:{_slug(name)}",
                "background",
                name,
                None,
                None,
                f"SRD 5.2.1 p.83: {name} background",
                "P04-O01",
                "origin.background",
                "catalogued",
            )
        )
    for name in SPECIES:
        records.append(
            CensusRecord(
                f"species:{name.casefold().replace(' ', '_')}",
                "species",
                name,
                None,
                None,
                f"SRD 5.2.1 p.{_PINNED_SRD_SPECIES_PAGES[name]}: {name}",
                "P04-O01",
                "origin.species",
                "catalogued",
            )
        )
    records.extend(_srd_feat_records())
    records.extend(_background_grant_records())
    records.extend(_pinned_srd_species_grant_records())
    records.extend(_initial_equipment_records())
    records.extend(_srd_tool_records())
    records.extend(_srd_adventuring_gear_records())
    records.extend(_srd_equipment_variant_records())
    records.extend(_mount_creature_records())
    records.extend(_srd_creature_stat_block_records())
    records.extend(_cantrip_records())
    records.extend(_level_one_spell_records())
    records.extend(_level_two_spell_records())
    records.extend(_level_three_spell_records())
    records.extend(_level_four_spell_records())
    records.extend(_level_five_spell_records())
    records.extend(_level_six_spell_records())
    records.extend(_level_seven_spell_records())
    records.extend(_level_eight_spell_records())
    records.extend(_level_nine_spell_records())
    return tuple(records)


_SRD_FEAT_ENTRIES = (
    ("origin", "alert", "Alert", "SRD 5.2.1 p.87: Alert"),
    ("origin", "magic_initiate", "Magic Initiate", "SRD 5.2.1 p.87: Magic Initiate"),
    ("origin", "savage_attacker", "Savage Attacker", "SRD 5.2.1 p.87: Savage Attacker"),
    ("origin", "skilled", "Skilled", "SRD 5.2.1 p.87: Skilled"),
    (
        "general",
        "ability_score_improvement",
        "Ability Score Improvement",
        "SRD 5.2.1 p.87: Ability Score Improvement",
    ),
    ("general", "grappler", "Grappler", "SRD 5.2.1 p.87: Grappler"),
    ("fighting_style", "archery", "Archery", "SRD 5.2.1 p.87: Archery"),
    ("fighting_style", "defense", "Defense", "SRD 5.2.1 p.88: Defense"),
    (
        "fighting_style",
        "great_weapon_fighting",
        "Great Weapon Fighting",
        "SRD 5.2.1 p.88: Great Weapon Fighting",
    ),
    (
        "fighting_style",
        "two_weapon_fighting",
        "Two-Weapon Fighting",
        "SRD 5.2.1 p.88: Two-Weapon Fighting",
    ),
    (
        "epic_boon",
        "boon_of_combat_prowess",
        "Boon of Combat Prowess",
        "SRD 5.2.1 p.88: Boon of Combat Prowess",
    ),
    (
        "epic_boon",
        "boon_of_dimensional_travel",
        "Boon of Dimensional Travel",
        "SRD 5.2.1 p.88: Boon of Dimensional Travel",
    ),
    ("epic_boon", "boon_of_fate", "Boon of Fate", "SRD 5.2.1 p.88: Boon of Fate"),
    (
        "epic_boon",
        "boon_of_irresistible_offense",
        "Boon of Irresistible Offense",
        "SRD 5.2.1 p.88: Boon of Irresistible Offense",
    ),
    (
        "epic_boon",
        "boon_of_spell_recall",
        "Boon of Spell Recall",
        "SRD 5.2.1 p.88: Boon of Spell Recall",
    ),
    (
        "epic_boon",
        "boon_of_the_night_spirit",
        "Boon of the Night Spirit",
        "SRD 5.2.1 p.88: Boon of the Night Spirit",
    ),
    (
        "epic_boon",
        "boon_of_truesight",
        "Boon of Truesight",
        "SRD 5.2.1 p.88: Boon of Truesight",
    ),
)


def _srd_feat_records() -> tuple[CensusRecord, ...]:
    """Return the complete, reviewed SRD feat and Epic Boon inventory.

    The pinned SRD's Feats section has four Origin feats, two General feats,
    four Fighting Style feats, and seven Epic Boon feats.  Category metadata
    remains an adaptation note until P04-A04 supplies the choice adapters.
    """
    return tuple(
        CensusRecord(
            f"feat:{key}",
            "feat",
            name,
            None,
            None,
            reference,
            "P04-A04",
            "advancement.choice",
            "catalogued",
            f"{category.replace('_', ' ')} feat",
        )
        for category, key, name, reference in _SRD_FEAT_ENTRIES
    )


def _background_grant_records() -> tuple[CensusRecord, ...]:
    """Project every pinned-SRD background's five concrete grant occurrences.

    The pinned SRD describes Acolyte, Criminal, Sage, and Soldier only. The
    twelve extra chargen backgrounds are local compatibility data, not SRD
    entries, and deliberately have no P04-A01 census record or fabricated
    source citation. Adding one requires an explicit source/scope revision.
    """
    records: list[CensusRecord] = []
    for background_name, background in _PINNED_SRD_BACKGROUNDS.items():
        background_key = _slug(background_name)
        reference = f"SRD 5.2.1 p.83: {background_name} background"
        entries = (
            (
                "ability_adjustments",
                "Ability adjustments: " + ", ".join(background["ability_options"]),
                "origin.ability_adjustment",
            ),
            (
                "skill_proficiencies",
                "Skill proficiencies: " + ", ".join(background["skill_proficiencies"]),
                "origin.skill_proficiencies",
            ),
            (
                "tool_proficiency",
                f"Tool proficiency: {background['tool_proficiency']}",
                "origin.tool_proficiency",
            ),
            ("feat", f"Origin feat: {background['feat']}", "origin.feat_grant"),
            (
                "equipment",
                f"Starting equipment: {background['equipment']}",
                "origin.starting_equipment",
            ),
        )
        records.extend(
            CensusRecord(
                f"origin_grant:background:{background_key}:{grant_key}",
                "origin_grant",
                f"{background_name} — {display_name}",
                None,
                None,
                reference,
                "P04-O01",
                adapter,
                "catalogued",
            )
            for grant_key, display_name, adapter in entries
        )
    return tuple(records)


_PINNED_SRD_BACKGROUNDS = MappingProxyType(
    {
        "Acolyte": {
            "ability_options": ("Intelligence", "Wisdom", "Charisma"),
            "skill_proficiencies": ("Insight", "Religion"),
            "tool_proficiency": "Calligrapher’s Supplies",
            "feat": "Magic Initiate (Cleric)",
            "equipment": "Choose A or B: (A) Calligrapher’s Supplies, Book (prayers), Holy Symbol, Parchment (10 sheets), Robe, 8 GP; or (B) 50 GP",
        },
        "Criminal": {
            "ability_options": ("Dexterity", "Constitution", "Intelligence"),
            "skill_proficiencies": ("Sleight of Hand", "Stealth"),
            "tool_proficiency": "Thieves’ Tools",
            "feat": "Alert",
            "equipment": "Choose A or B: (A) 2 Daggers, Thieves’ Tools, Crowbar, 2 Pouches, Traveler’s Clothes, 16 GP; or (B) 50 GP",
        },
        "Sage": {
            "ability_options": ("Constitution", "Intelligence", "Wisdom"),
            "skill_proficiencies": ("Arcana", "History"),
            "tool_proficiency": "Calligrapher’s Supplies",
            "feat": "Magic Initiate (Wizard)",
            "equipment": "Choose A or B: (A) Quarterstaff, Calligrapher’s Supplies, Book (history), Parchment (8 sheets), Robe, 8 GP; or (B) 50 GP",
        },
        "Soldier": {
            "ability_options": ("Strength", "Dexterity", "Constitution"),
            "skill_proficiencies": ("Athletics", "Intimidation"),
            "tool_proficiency": "Choose one kind of Gaming Set",
            "feat": "Savage Attacker",
            "equipment": "Choose A or B: (A) Spear, Shortbow, 20 Arrows, Gaming Set (same as above), Healer’s Kit, Quiver, Traveler’s Clothes, 14 GP; or (B) 50 GP",
        },
    }
)


_PINNED_SRD_SPECIES_GRANTS = (
    # Each species grants creature type, size, and speed when selected.
    (
        "Dragonborn",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        84,
    ),
    ("Dragonborn", 1, "size", "Size: Medium", "origin.size", 84),
    ("Dragonborn", 1, "speed", "Speed: 30 feet", "origin.speed", 84),
    (
        "Dragonborn",
        1,
        "draconic_ancestry",
        "Draconic Ancestry choice",
        "origin.choice",
        84,
    ),
    (
        "Dragonborn",
        1,
        "breath_weapon",
        "Breath Weapon (1d10)",
        "origin.limited_use_trait",
        84,
    ),
    (
        "Dragonborn",
        1,
        "breath_weapon_uses",
        "Breath Weapon uses (Proficiency Bonus per Long Rest)",
        "origin.resource",
        84,
    ),
    (
        "Dragonborn",
        1,
        "damage_resistance",
        "Damage Resistance (Draconic Ancestry type)",
        "origin.resistance",
        84,
    ),
    ("Dragonborn", 1, "darkvision", "Darkvision (60 feet)", "origin.sense", 84),
    (
        "Dragonborn",
        5,
        "breath_weapon_2d10",
        "Breath Weapon improvement (2d10)",
        "origin.limited_use_trait",
        84,
    ),
    (
        "Dragonborn",
        5,
        "draconic_flight",
        "Draconic Flight",
        "origin.limited_use_trait",
        84,
    ),
    (
        "Dragonborn",
        11,
        "breath_weapon_3d10",
        "Breath Weapon improvement (3d10)",
        "origin.limited_use_trait",
        84,
    ),
    (
        "Dragonborn",
        17,
        "breath_weapon_4d10",
        "Breath Weapon improvement (4d10)",
        "origin.limited_use_trait",
        84,
    ),
    (
        "Dwarf",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        84,
    ),
    ("Dwarf", 1, "size", "Size: Medium", "origin.size", 84),
    ("Dwarf", 1, "speed", "Speed: 30 feet", "origin.speed", 84),
    ("Dwarf", 1, "darkvision", "Darkvision (120 feet)", "origin.sense", 84),
    ("Dwarf", 1, "dwarven_resilience", "Dwarven Resilience", "origin.resistance", 84),
    ("Dwarf", 1, "stonecunning", "Stonecunning", "origin.limited_use_trait", 84),
    ("Elf", 1, "creature_type", "Creature Type: Humanoid", "origin.creature_type", 84),
    ("Elf", 1, "size", "Size: Medium", "origin.size", 84),
    ("Elf", 1, "speed", "Speed: 30 feet", "origin.speed", 84),
    ("Elf", 1, "darkvision", "Darkvision (60 feet)", "origin.sense", 84),
    ("Elf", 1, "elven_lineage", "Elven Lineage choice", "origin.choice", 84),
    (
        "Elf",
        1,
        "drow_lineage",
        "Drow lineage: 120-foot Darkvision and Dancing Lights",
        "origin.choice",
        85,
    ),
    (
        "Elf",
        3,
        "drow_faerie_fire",
        "Drow lineage: Faerie Fire",
        "origin.innate_spell",
        85,
    ),
    ("Elf", 5, "drow_darkness", "Drow lineage: Darkness", "origin.innate_spell", 85),
    (
        "Elf",
        1,
        "high_elf_lineage",
        "High Elf lineage: Prestidigitation replacement",
        "origin.choice",
        85,
    ),
    (
        "Elf",
        3,
        "high_elf_detect_magic",
        "High Elf lineage: Detect Magic",
        "origin.innate_spell",
        85,
    ),
    (
        "Elf",
        5,
        "high_elf_misty_step",
        "High Elf lineage: Misty Step",
        "origin.innate_spell",
        85,
    ),
    (
        "Elf",
        1,
        "wood_elf_lineage",
        "Wood Elf lineage: 35-foot Speed and Druidcraft",
        "origin.choice",
        85,
    ),
    (
        "Elf",
        3,
        "wood_elf_longstrider",
        "Wood Elf lineage: Longstrider",
        "origin.innate_spell",
        85,
    ),
    (
        "Elf",
        5,
        "wood_elf_pass_without_trace",
        "Wood Elf lineage: Pass without Trace",
        "origin.innate_spell",
        85,
    ),
    (
        "Elf",
        1,
        "lineage_spellcasting_ability",
        "Lineage spellcasting ability choice",
        "origin.choice",
        85,
    ),
    ("Elf", 1, "fey_ancestry", "Fey Ancestry", "origin.condition_defense", 85),
    ("Elf", 1, "keen_senses", "Keen Senses skill choice", "origin.skill_choice", 85),
    ("Elf", 1, "trance", "Trance", "origin.rest_trait", 85),
    (
        "Gnome",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        85,
    ),
    ("Gnome", 1, "size", "Size: Small", "origin.size", 85),
    ("Gnome", 1, "speed", "Speed: 30 feet", "origin.speed", 85),
    ("Gnome", 1, "darkvision", "Darkvision (60 feet)", "origin.sense", 85),
    ("Gnome", 1, "gnomish_cunning", "Gnomish Cunning", "origin.save_defense", 85),
    ("Gnome", 1, "gnomish_lineage", "Gnomish Lineage choice", "origin.choice", 85),
    (
        "Gnome",
        1,
        "forest_gnome_lineage",
        "Forest Gnome: Minor Illusion and Speak with Animals",
        "origin.choice",
        85,
    ),
    (
        "Gnome",
        1,
        "rock_gnome_lineage",
        "Rock Gnome: Mending, Prestidigitation, and clockwork device",
        "origin.choice",
        85,
    ),
    (
        "Goliath",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        85,
    ),
    ("Goliath", 1, "size", "Size: Medium", "origin.size", 85),
    ("Goliath", 1, "speed", "Speed: 35 feet", "origin.speed", 85),
    ("Goliath", 1, "giant_ancestry", "Giant Ancestry choice", "origin.choice", 85),
    ("Goliath", 1, "clouds_jaunt", "Cloud's Jaunt (Cloud Giant)", "origin.choice", 85),
    ("Goliath", 1, "fires_burn", "Fire's Burn (Fire Giant)", "origin.choice", 85),
    ("Goliath", 1, "frosts_chill", "Frost's Chill (Frost Giant)", "origin.choice", 85),
    ("Goliath", 1, "hills_tumble", "Hill's Tumble (Hill Giant)", "origin.choice", 85),
    (
        "Goliath",
        1,
        "stones_endurance",
        "Stone's Endurance (Stone Giant)",
        "origin.choice",
        85,
    ),
    (
        "Goliath",
        1,
        "storms_thunder",
        "Storm's Thunder (Storm Giant)",
        "origin.choice",
        86,
    ),
    ("Goliath", 5, "large_form", "Large Form", "origin.limited_use_trait", 86),
    ("Goliath", 1, "powerful_build", "Powerful Build", "origin.carrying_capacity", 86),
    (
        "Halfling",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        86,
    ),
    ("Halfling", 1, "size", "Size: Small", "origin.size", 86),
    ("Halfling", 1, "speed", "Speed: 30 feet", "origin.speed", 86),
    ("Halfling", 1, "brave", "Brave", "origin.condition_defense", 86),
    (
        "Halfling",
        1,
        "halfling_nimbleness",
        "Halfling Nimbleness",
        "origin.movement",
        86,
    ),
    ("Halfling", 1, "luck", "Luck", "origin.roll_modifier", 86),
    ("Halfling", 1, "naturally_stealthy", "Naturally Stealthy", "origin.hide_rule", 86),
    (
        "Human",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        86,
    ),
    ("Human", 1, "size", "Size choice: Medium or Small", "origin.size", 86),
    ("Human", 1, "speed", "Speed: 30 feet", "origin.speed", 86),
    ("Human", 1, "resourceful", "Resourceful", "origin.rest_trait", 86),
    ("Human", 1, "skillful", "Skillful skill choice", "origin.skill_choice", 86),
    ("Human", 1, "versatile", "Versatile Origin Feat choice", "origin.feat_choice", 86),
    ("Orc", 1, "creature_type", "Creature Type: Humanoid", "origin.creature_type", 86),
    ("Orc", 1, "size", "Size: Medium", "origin.size", 86),
    ("Orc", 1, "speed", "Speed: 30 feet", "origin.speed", 86),
    ("Orc", 1, "adrenaline_rush", "Adrenaline Rush", "origin.limited_use_trait", 86),
    ("Orc", 1, "darkvision", "Darkvision (120 feet)", "origin.sense", 86),
    ("Orc", 1, "powerful_build", "Powerful Build", "origin.carrying_capacity", 86),
    (
        "Orc",
        1,
        "relentless_endurance",
        "Relentless Endurance",
        "origin.limited_use_trait",
        86,
    ),
    (
        "Tiefling",
        1,
        "creature_type",
        "Creature Type: Humanoid",
        "origin.creature_type",
        86,
    ),
    ("Tiefling", 1, "size", "Size choice: Medium or Small", "origin.size", 86),
    ("Tiefling", 1, "speed", "Speed: 30 feet", "origin.speed", 86),
    ("Tiefling", 1, "darkvision", "Darkvision (60 feet)", "origin.sense", 86),
    ("Tiefling", 1, "fiendish_legacy", "Fiendish Legacy choice", "origin.choice", 86),
    (
        "Tiefling",
        1,
        "abyssal_legacy",
        "Abyssal Legacy: Poison Resistance and Poison Spray",
        "origin.choice",
        86,
    ),
    (
        "Tiefling",
        3,
        "abyssal_ray_of_sickness",
        "Abyssal Legacy: Ray of Sickness",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        5,
        "abyssal_hold_person",
        "Abyssal Legacy: Hold Person",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        1,
        "chthonic_legacy",
        "Chthonic Legacy: Necrotic Resistance and Chill Touch",
        "origin.choice",
        86,
    ),
    (
        "Tiefling",
        3,
        "chthonic_false_life",
        "Chthonic Legacy: False Life",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        5,
        "chthonic_ray_of_enfeeblement",
        "Chthonic Legacy: Ray of Enfeeblement",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        1,
        "infernal_legacy",
        "Infernal Legacy: Fire Resistance and Fire Bolt",
        "origin.choice",
        86,
    ),
    (
        "Tiefling",
        3,
        "infernal_hellish_rebuke",
        "Infernal Legacy: Hellish Rebuke",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        5,
        "infernal_darkness",
        "Infernal Legacy: Darkness",
        "origin.innate_spell",
        86,
    ),
    (
        "Tiefling",
        1,
        "legacy_spellcasting_ability",
        "Legacy spellcasting ability choice",
        "origin.choice",
        86,
    ),
    (
        "Tiefling",
        1,
        "otherworldly_presence",
        "Otherworldly Presence",
        "origin.innate_spell",
        86,
    ),
)

_PINNED_SRD_SPECIES_PAGES = MappingProxyType(
    {
        "Dragonborn": 84,
        "Dwarf": 84,
        "Elf": 84,
        "Gnome": 85,
        "Goliath": 85,
        "Halfling": 86,
        "Human": 86,
        "Orc": 86,
        "Tiefling": 86,
    }
)


def _pinned_srd_species_grant_records() -> tuple[CensusRecord, ...]:
    """Project the reviewed SRD species grants, choices, and level gates.

    Entries document availability and intended adapter ownership only.  They do
    not grant a trait to a character or claim that the corresponding mechanic is
    available before P04-O01 and its dependent adapters are released.
    """
    fixture_species = {entry[0] for entry in _PINNED_SRD_SPECIES_GRANTS}
    if fixture_species != set(SPECIES):
        raise ContentCensusError(
            "Pinned SRD species fixture does not match chargen species: "
            + ", ".join(sorted(fixture_species ^ set(SPECIES)))
        )
    records = [
        CensusRecord(
            f"origin_grant:species:{_slug(species)}:{key}:level:{level}",
            "origin_grant",
            f"{species} — {display_name}",
            None,
            level,
            f"SRD 5.2.1 p.{page}: {species}",
            "P04-O01",
            adapter,
            "catalogued",
            "species grant; mechanics remain unavailable",
        )
        for species, level, key, display_name, adapter, page in _PINNED_SRD_SPECIES_GRANTS
    ]
    records.extend(
        CensusRecord(
            f"origin_grant:species:dwarf:dwarven_toughness:level:{level}",
            "origin_grant",
            f"Dwarf — Dwarven Toughness (+1 maximum HP at character level {level})",
            None,
            level,
            "SRD 5.2.1 p.84: Dwarf — Dwarven Toughness",
            "P04-O01",
            "origin.hit_point_progression",
            "catalogued",
            "repeated species grant; mechanics remain unavailable",
        )
        for level in range(1, MAX_CLASS_LEVEL + 1)
    )
    return tuple(records)


def _initial_equipment_records() -> tuple[CensusRecord, ...]:
    """Catalogue the cited SRD weapon and armor table references.

    These records intentionally describe required equipment references rather
    than claiming that every weapon property or armor interaction is released.
    P04-A05 owns their mechanics and promotion.
    """
    weapons = (
        "Club",
        "Dagger",
        "Greatclub",
        "Handaxe",
        "Javelin",
        "Light Hammer",
        "Mace",
        "Quarterstaff",
        "Sickle",
        "Spear",
        "Dart",
        "Light Crossbow",
        "Shortbow",
        "Sling",
        "Battleaxe",
        "Flail",
        "Glaive",
        "Greataxe",
        "Greatsword",
        "Halberd",
        "Lance",
        "Longsword",
        "Maul",
        "Morningstar",
        "Pike",
        "Rapier",
        "Scimitar",
        "Shortsword",
        "Trident",
        "Warhammer",
        "War Pick",
        "Whip",
        "Blowgun",
        "Hand Crossbow",
        "Heavy Crossbow",
        "Longbow",
        "Musket",
        "Pistol",
    )
    armor = (
        "Padded Armor",
        "Leather Armor",
        "Studded Leather Armor",
        "Hide Armor",
        "Chain Shirt",
        "Scale Mail",
        "Breastplate",
        "Half Plate Armor",
        "Ring Mail",
        "Chain Mail",
        "Splint Armor",
        "Plate Armor",
        "Shield",
    )
    return tuple(
        CensusRecord(
            f"equipment:weapon:{_slug(name)}",
            "equipment_reference",
            name,
            None,
            None,
            "SRD 5.2.1 p.90: Weapons table",
            "P04-A05",
            "equipment.weapon",
            "catalogued",
        )
        for name in weapons
    ) + tuple(
        CensusRecord(
            f"equipment:armor:{_slug(name)}",
            "equipment_reference",
            name,
            None,
            None,
            "SRD 5.2.1 p.91: Armor table",
            "P04-A05",
            "equipment.armor",
            "catalogued",
        )
        for name in armor
    )


def _srd_tool_records() -> tuple[CensusRecord, ...]:
    """Catalogue every named tool and separately proficient variant in the SRD.

    This source slice deliberately inventories tools and variants only; their
    checks, crafting, and utilization adapters remain P04-A05 work.
    """
    artisan_tools = (
        "Alchemist’s Supplies",
        "Brewer’s Supplies",
        "Calligrapher’s Supplies",
        "Carpenter’s Tools",
        "Cartographer’s Tools",
        "Cobbler’s Tools",
        "Cook’s Utensils",
        "Glassblower’s Tools",
        "Jeweler’s Tools",
        "Leatherworker’s Tools",
        "Mason’s Tools",
        "Painter’s Supplies",
        "Potter’s Tools",
        "Smith’s Tools",
        "Tinker’s Tools",
        "Weaver’s Tools",
        "Woodcarver’s Tools",
    )
    other_tools = (
        "Disguise Kit",
        "Forgery Kit",
        "Gaming Set",
        "Herbalism Kit",
        "Musical Instrument",
        "Navigator’s Tools",
        "Poisoner’s Kit",
        "Thieves’ Tools",
    )
    variants = (
        "Dice",
        "Dragonchess",
        "Playing Cards",
        "Three-Dragon Ante",
        "Bagpipes",
        "Drum",
        "Dulcimer",
        "Flute",
        "Horn",
        "Lute",
        "Lyre",
        "Pan Flute",
        "Shawm",
        "Viol",
    )
    return tuple(
        CensusRecord(
            f"equipment:tool:{_slug(name)}",
            "equipment_reference",
            name,
            None,
            None,
            "SRD 5.2.1 pp.92-93: Tools",
            "P04-A05",
            "equipment.tool",
            "catalogued",
            adaptation,
        )
        for name, adaptation in (
            *((name, "artisan tool") for name in artisan_tools),
            *((name, "other tool") for name in other_tools),
            *((name, "tool variant") for name in variants),
        )
    )


def _srd_adventuring_gear_records() -> tuple[CensusRecord, ...]:
    """Catalogue every named row in the pinned SRD Adventuring Gear table."""
    entries = (
        "Acid",
        "Alchemist’s Fire",
        "Ammunition",
        "Antitoxin",
        "Arcane Focus",
        "Backpack",
        "Ball Bearings",
        "Barrel",
        "Basket",
        "Bedroll",
        "Bell",
        "Blanket",
        "Block and Tackle",
        "Book",
        "Bottle, Glass",
        "Bucket",
        "Burglar’s Pack",
        "Caltrops",
        "Candle",
        "Case, Crossbow Bolt",
        "Case, Map or Scroll",
        "Chain",
        "Chest",
        "Climber’s Kit",
        "Clothes, Fine",
        "Clothes, Traveler’s",
        "Component Pouch",
        "Costume",
        "Crowbar",
        "Diplomat’s Pack",
        "Druidic Focus",
        "Dungeoneer’s Pack",
        "Entertainer’s Pack",
        "Explorer’s Pack",
        "Flask",
        "Grappling Hook",
        "Healer’s Kit",
        "Holy Symbol",
        "Holy Water",
        "Hunting Trap",
        "Ink",
        "Ink Pen",
        "Jug",
        "Ladder",
        "Lamp",
        "Lantern, Bullseye",
        "Lantern, Hooded",
        "Lock",
        "Magnifying Glass",
        "Manacles",
        "Map",
        "Mirror",
        "Net",
        "Oil",
        "Paper",
        "Parchment",
        "Perfume",
        "Poison, Basic",
        "Pole",
        "Pot, Iron",
        "Potion of Healing",
        "Pouch",
        "Priest’s Pack",
        "Quiver",
        "Ram, Portable",
        "Rations",
        "Robe",
        "Rope",
        "Sack",
        "Scholar’s Pack",
        "Shovel",
        "Signal Whistle",
        "Spell Scroll (Cantrip)",
        "Spell Scroll (Level 1)",
        "Spikes, Iron",
        "Spyglass",
        "String",
        "Tent",
        "Tinderbox",
        "Torch",
        "Vial",
        "Waterskin",
    )
    return tuple(
        CensusRecord(
            f"equipment:adventuring_gear:{_slug(name)}",
            "equipment_reference",
            name,
            None,
            None,
            "SRD 5.2.1 p.94: Adventuring Gear table",
            "P04-A05",
            "equipment.adventuring_gear",
            "catalogued",
        )
        for name in entries
    )


def _srd_equipment_variant_records() -> tuple[CensusRecord, ...]:
    """Catalogue remaining named equipment rows and the Barding rule."""
    entries = (
        (
            "ammunition",
            "SRD 5.2.1 p.95: Ammunition table",
            "equipment.ammunition",
            "Arrows",
            "Arrows",
        ),
        (
            "ammunition",
            "SRD 5.2.1 p.95: Ammunition table",
            "equipment.ammunition",
            "Bolts",
            "Bolts",
        ),
        (
            "ammunition",
            "SRD 5.2.1 p.95: Ammunition table",
            "equipment.ammunition",
            "Bullets, Firearm",
            "Bullets, Firearm",
        ),
        (
            "ammunition",
            "SRD 5.2.1 p.95: Ammunition table",
            "equipment.ammunition",
            "Bullets, Sling",
            "Bullets, Sling",
        ),
        (
            "ammunition",
            "SRD 5.2.1 p.95: Ammunition table",
            "equipment.ammunition",
            "Needles",
            "Needles",
        ),
        (
            "focus",
            "SRD 5.2.1 p.95: Arcane Focuses table",
            "equipment.spellcasting_focus",
            "Crystal",
            "Arcane Focus: Crystal",
        ),
        (
            "focus",
            "SRD 5.2.1 p.95: Arcane Focuses table",
            "equipment.spellcasting_focus",
            "Orb",
            "Arcane Focus: Orb",
        ),
        (
            "focus",
            "SRD 5.2.1 p.95: Arcane Focuses table",
            "equipment.spellcasting_focus",
            "Rod",
            "Arcane Focus: Rod",
        ),
        (
            "focus",
            "SRD 5.2.1 p.95: Arcane Focuses table",
            "equipment.spellcasting_focus",
            "Staff",
            "Arcane Focus: Staff",
        ),
        (
            "focus",
            "SRD 5.2.1 p.95: Arcane Focuses table",
            "equipment.spellcasting_focus",
            "Wand",
            "Arcane Focus: Wand",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Druidic Focuses table",
            "equipment.spellcasting_focus",
            "Sprig of Mistletoe",
            "Druidic Focus: Sprig of Mistletoe",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Druidic Focuses table",
            "equipment.spellcasting_focus",
            "Wooden Staff",
            "Druidic Focus: Wooden Staff",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Druidic Focuses table",
            "equipment.spellcasting_focus",
            "Yew Wand",
            "Druidic Focus: Yew Wand",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Holy Symbols table",
            "equipment.spellcasting_focus",
            "Amulet",
            "Holy Symbol: Amulet",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Holy Symbols table",
            "equipment.spellcasting_focus",
            "Emblem",
            "Holy Symbol: Emblem",
        ),
        (
            "focus",
            "SRD 5.2.1 p.96: Holy Symbols table",
            "equipment.spellcasting_focus",
            "Reliquary",
            "Holy Symbol: Reliquary",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.vehicle",
            "Carriage",
            "Carriage",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.vehicle",
            "Cart",
            "Cart",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.vehicle",
            "Chariot",
            "Chariot",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.mount_care",
            "Feed per Day",
            "Feed per Day",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.saddle",
            "Saddle, Exotic",
            "Saddle, Exotic",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.saddle",
            "Saddle, Military",
            "Saddle, Military",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.saddle",
            "Saddle, Riding",
            "Saddle, Riding",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.vehicle",
            "Sled",
            "Sled",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.mount_care",
            "Stabling per Day",
            "Stabling per Day",
        ),
        (
            "tack",
            "SRD 5.2.1 p.99: Tack, Harness, and Drawn Vehicles table",
            "equipment.vehicle",
            "Wagon",
            "Wagon",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Airship",
            "Airship",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Galley",
            "Galley",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Keelboat",
            "Keelboat",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Longship",
            "Longship",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Rowboat",
            "Rowboat",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Sailing Ship",
            "Sailing Ship",
        ),
        (
            "vehicle",
            "SRD 5.2.1 p.100: Airborne and Waterborne Vehicles table",
            "equipment.vehicle",
            "Warship",
            "Warship",
        ),
        (
            "mount_equipment",
            "SRD 5.2.1 p.100: Mounts and Vehicles — Barding",
            "equipment.barding",
            "Barding",
            "Barding",
        ),
    )
    return tuple(
        CensusRecord(
            f"equipment:{category}:{_slug(key)}",
            "equipment_reference",
            display_name,
            None,
            None,
            reference,
            "P04-A05",
            adapter,
            "catalogued",
        )
        for category, reference, adapter, key, display_name in entries
    )


def _mount_creature_records() -> tuple[CensusRecord, ...]:
    """Catalogue creatures referenced by the SRD mounts-and-animals table."""
    mounts = (
        "Camel",
        "Elephant",
        "Horse, Draft",
        "Horse, Riding",
        "Mastiff",
        "Mule",
        "Pony",
        "Warhorse",
    )
    return tuple(
        CensusRecord(
            f"creature:mount:{_slug(name)}",
            "creature_reference",
            name,
            None,
            None,
            "SRD 5.2.1 p.99: Mounts and Other Animals table",
            "P04-A08",
            "world.creature_reference",
            "catalogued",
        )
        for name in mounts
    )


_SRD_CREATURE_STAT_BLOCKS = """\
Aboleth|258
Adult Black Dragon|264
Adult Blue Dragon|266
Adult Brass Dragon|268
Adult Bronze Dragon|270
Adult Copper Dragon|276
Adult Gold Dragon|291
Adult Green Dragon|294
Adult Red Dragon|318
Adult Silver Dragon|324
Adult White Dragon|340
Air Elemental|258
Allosaurus|344
Ancient Black Dragon|265
Ancient Blue Dragon|267
Ancient Brass Dragon|269
Ancient Bronze Dragon|271
Ancient Copper Dragon|277
Ancient Gold Dragon|292
Ancient Green Dragon|294
Ancient Red Dragon|319
Ancient Silver Dragon|325
Ancient White Dragon|341
Animated Armor|259
Animated Flying Sword|259
Animated Rug of Smothering|259
Ankheg|259
Dryad|282
Guard|296
Ankylosaurus|344
Dust Mephit|307
Guard Captain|296
Ape|344
Eagle|348
Guardian Naga|296
Archelon|344
Earth Elemental|282
Half-Dragon|297
Archmage|305
Efreeti|283
Harpy|297
Assassin|260
Elephant|348
Hawk|355
Awakened Shrub|260
Elk|348
Hell Hound|297
Awakened Tree|260
Erinyes|283
Hezrou|298
Axe Beak|260
Ettercap|284
Hill Giant|298
Azer Sentinel|261
Ettin|284
Hippogriff|298
Baboon|345
Fire Elemental|284
Hippopotamus|355
Badger|345
Fire Giant|285
Hobgoblin Captain|299
Balor|261
Flesh Golem|285
Hobgoblin Warrior|298
Bandit|261
Flying Snake|348
Homunculus|299
Bandit Captain|261
Frog|348
Horned Devil|299
Barbed Devil|262
Frost Giant|285
Hunter Shark|356
Basilisk|262
Gargoyle|286
Hydra|300
Bat|345
Gelatinous Cube|286
Hyena|356
Bearded Devil|262
Ghast|287
Ice Devil|300
Behir|263
Ghost|287
Ice Mephit|307
Berserker|263
Ghoul|288
Imp|300
Black Bear|345
Giant Ape|349
Incubus|301
Black Dragon Wyrmling|263
Giant Badger|349
Invisible Stalker|301
Black Pudding|265
Giant Bat|349
Iron Golem|302
Blink Dog|266
Giant Boar|349
Jackal|356
Blood Hawk|345
Giant Centipede|349
Killer Whale|356
Blue Dragon Wyrmling|266
Giant Constrictor Snake|350
Knight|302
Boar|346
Giant Crab|350
Kobold Warrior|302
Bone Devil|267
Giant Crocodile|350
Kraken|303
Brass Dragon Wyrmling|268
Giant Eagle|350
Lamia|303
Bronze Dragon Wyrmling|269
Giant Elk|351
Lemure|304
Brown Bear|346
Giant Fire Beetle|351
Lich|304
Bugbear Stalker|271
Giant Frog|351
Lion|356
Bugbear Warrior|272
Giant Goat|351
Lizard|357
Bulette|272
Giant Hyena|352
Mage|305
Camel|346
Giant Lizard|352
Magma Mephit|307
Cat|346
Giant Octopus|352
Magmin|305
Centaur Trooper|272
Giant Owl|352
Mammoth|357
Chain Devil|272
Giant Rat|353
Manticore|306
Chimera|273
Giant Scorpion|353
Marilith|306
Chuul|273
Giant Seahorse|353
Mastiff|357
Clay Golem|274
Giant Shark|353
Medusa|306
Cloaker|274
Giant Spider|353
Merfolk Skirmisher|308
Cloud Giant|275
Giant Toad|354
Merrow|308
Cockatrice|275
Giant Venomous Snake|354
Mimic|308
Commoner|275
Giant Vulture|354
Minotaur of Baphomet|309
Constrictor Snake|346
Giant Wasp|354
Minotaur Skeleton|326
Copper Dragon Wyrmling|275
Giant Weasel|355
Mule|357
Couatl|277
Giant Wolf Spider|355
Mummy|309
Crab|347
Gibbering Mouther|288
Mummy Lord|309
Crocodile|347
Glabrezu|289
Nalfeshnee|310
Cultist|278
Gladiator|289
Night Hag|311
Cultist Fanatic|278
Gnoll Warrior|289
Nightmare|311
Darkmantle|278
Goat|355
Noble|312
Death Dog|279
Goblin Boss|290
Ochre Jelly|312
Deer|347
Goblin Minion|290
Octopus|357
Deva|279
Goblin Warrior|290
Ogre|312
Dire Wolf|347
Gold Dragon Wyrmling|290
Ogre Zombie|344
Djinni|280
Gorgon|292
Oni|312
Doppelganger|280
Gray Ooze|293
Otyugh|313
Draft Horse|347
Green Dragon Wyrmling|293
Owl|358
Dragon Turtle|281
Green Hag|295
Owlbear|313
Dretch|281
Grick|295
Panther|358
Drider|281
Griffon|295
Pegasus|313
Druid|282
Grimlock|296
Phase Spider|313
Piranha|358
Treant|333
Pirate|314
Triceratops|363
Pirate Captain|314
Troll|333
Pit Fiend|314
Troll Limb|333
Planetar|315
Tyrannosaurus Rex|363
Plesiosaurus|358
Unicorn|334
Polar Bear|359
Vampire|335
Pony|359
Vampire Familiar|334
Priest|316
Vampire Spawn|334
Priest Acolyte|316
Venomous Snake|363
Pseudodragon|316
Violet Fungus|286
Pteranodon|359
Vrock|336
Purple Worm|316
Vulture|363
Quasit|317
Warhorse|364
Rakshasa|317
Warhorse Skeleton|326
Rat|359
Warrior Infantry|336
Raven|359
Warrior Veteran|337
Red Dragon Wyrmling|318
Water Elemental|337
Reef Shark|360
Weasel|364
Remorhaz|319
Werebear|337
Rhinoceros|360
Wereboar|338
Riding Horse|360
Wererat|338
Roc|320
Weretiger|339
Roper|320
Werewolf|339
Rust Monster|320
White Dragon Wyrmling|339
Saber-Toothed Tiger|360
Wight|341
Sahuagin Warrior|321
Will-o’-Wisp|341
Salamander|321
Winter Wolf|342
Satyr|321
Wolf|364
Scorpion|360
Worg|342
Scout|322
Wraith|342
Sea Hag|322
Wyvern|343
Seahorse|361
Xorn|343
Shadow|322
Young Black Dragon|264
Shambling Mound|323
Young Blue Dragon|266
Shield Guardian|323
Young Brass Dragon|268
Shrieker Fungus|286
Young Bronze Dragon|270
Silver Dragon Wyrmling|324
Young Copper Dragon|276
Skeleton|325
Young Gold Dragon|291
Solar|326
Young Green Dragon|293
Specter|327
Young Red Dragon|318
Sphinx of Lore|327
Young Silver Dragon|324
Sphinx of Valor|328
Young White Dragon|340
Sphinx of Wonder|327
Zombie|343
Spider|361
Spirit Naga|329
Sprite|329
Spy|329
Steam Mephit|308
Stirge|329
Stone Giant|330
Stone Golem|330
Storm Giant|330
Succubus|331
Swarm of Bats|361
Swarm of Crawling Claws|278
Swarm of Insects|361
Swarm of Piranhas|362
Swarm of Rats|362
Swarm of Ravens|362
Swarm of Venomous Snakes|362
Tarrasque|331
Tiger|363
Tough|332
Tough Boss|332
"""


def _srd_creature_stat_block_records() -> tuple[CensusRecord, ...]:
    """Catalogue every monster and animal listed in the pinned SRD index."""
    entries = tuple(
        row.rsplit("|", maxsplit=1)
        for row in _SRD_CREATURE_STAT_BLOCKS.rstrip().splitlines()
    )
    return tuple(
        CensusRecord(
            f"creature:stat_block:{_slug(name)}",
            "creature_reference",
            name,
            None,
            None,
            f"SRD 5.2.1 p.{page}: {name} stat block",
            "P04-A08",
            "world.creature_reference",
            "catalogued",
            "stat-block reference only; mechanics remain unavailable",
        )
        for name, page in entries
    )


def _slug(value: str) -> str:
    """Return a deterministic non-executable component of a census key."""
    return value.casefold().replace(" ", "_")


def _cantrip_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S00 class-list rows without publishing a spell."""
    return _spell_records(SRD_CANTRIP_LISTS, "P04-S00")


def _level_one_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S01 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_ONE_SPELL_LISTS, "P04-S01")


def _level_two_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S02 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_TWO_SPELL_LISTS, "P04-S02")


def _level_three_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S03 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_THREE_SPELL_LISTS, "P04-S03")


def _level_four_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S04 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_FOUR_SPELL_LISTS, "P04-S04")


def _level_five_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S05 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_FIVE_SPELL_LISTS, "P04-S05")


def _level_six_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S06 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_SIX_SPELL_LISTS, "P04-S06")


def _level_seven_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S07 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_SEVEN_SPELL_LISTS, "P04-S07")


def _level_eight_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S08 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_EIGHT_SPELL_LISTS, "P04-S08")


def _level_nine_spell_records() -> tuple[CensusRecord, ...]:
    """Project all P04-S09 class-list rows without publishing a spell."""
    return _spell_records(SRD_LEVEL_NINE_SPELL_LISTS, "P04-S09")


def _spell_records(
    lists: Mapping[str, tuple[SRDSpellListEntry, ...]], owner_task: str
) -> tuple[CensusRecord, ...]:
    """Convert reviewed source rows into catalogue-only census records."""
    return tuple(
        CensusRecord(
            f"spell:{entry.class_key.casefold()}:{_slug(entry.spell_name)}:level:{entry.spell_level}",
            "spell",
            entry.spell_name,
            entry.class_key,
            entry.spell_level,
            entry.srd_reference,
            owner_task,
            "magic.spell",
            "catalogued",
        )
        for entries in lists.values()
        for entry in entries
    )


def _occurrence_key(feature_key: str, level: int) -> str:
    """Keep repeats/upgrades as distinct cited source occurrences."""
    return f"feature:{feature_key}:level:{level}"


SRD_CONTENT_CENSUS = build_content_census(_default_records())

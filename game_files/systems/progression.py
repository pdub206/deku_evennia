"""Immutable, validated class progression for the Milestone 3 alpha."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Iterable, Mapping

MAX_CLASS_LEVEL = 3
CLASS_REGISTRY_VERSION = 3
SELECTABLE_CLASS_NAMES = ("Cleric", "Fighter", "Rogue", "Wizard")
UNAVAILABLE_CLASS_NAMES = (
    "Barbarian",
    "Bard",
    "Druid",
    "Monk",
    "Paladin",
    "Ranger",
    "Sorcerer",
    "Warlock",
)


class RegistryValidationError(ValueError):
    """Raised when progression data is unsafe to expose."""


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    owner: str
    prerequisites: tuple[str, ...]
    grant_mode: str
    repeat_mode: str
    help_key: str
    srd_reference: str = "SRD 5.2.1 class feature"


@dataclass(frozen=True)
class ResourceProgression:
    key: str
    owner: str
    maxima: tuple[int, ...]
    recovery_profile: str
    srd_reference: str = "SRD 5.2.1 class features table"


@dataclass(frozen=True)
class SpellAccess:
    key: str
    casting_ability: str
    cantrips: tuple[int, ...]
    spells_known: tuple[int, ...]
    spells_prepared: tuple[int, ...]
    spellbook_entries: tuple[int, ...]
    maximum_spell_level: tuple[int, ...]
    spell_slots: tuple[tuple[int, ...], ...]
    pact_slots: tuple[int, ...]
    pact_slot_level: tuple[int, ...]
    srd_reference: str
    preparation_formula: str | None = None
    preparation_timing: str = "long_rest"


@dataclass(frozen=True)
class ChoiceSet:
    key: str
    count: int
    legal_options: tuple[str, ...]
    replacement_policy: str
    mutual_exclusions: tuple[tuple[str, ...], ...]
    prerequisite_timing: str
    option_adapter: str = "skill"
    srd_reference: str = "SRD 5.2.1 class feature"


@dataclass(frozen=True)
class LevelGrants:
    level: int
    automatic_feature_keys: tuple[str, ...] = ()
    resource_keys: tuple[str, ...] = ()
    spell_access_keys: tuple[str, ...] = ()
    choice_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassDefinition:
    key: str
    display_name: str
    likes: str
    primary_abilities: tuple[str, ...]
    spellcasting_ability: str | None
    hit_die: int
    fixed_hp_gain: int
    saving_throws: tuple[str, ...]
    armor_training: tuple[str, ...]
    weapon_categories: tuple[str, ...]
    weapon_proficiencies: tuple[str, ...]
    weapon_profs: str
    complexity: str
    skill_choice_key: str
    srd_reference: str
    levels: tuple[LevelGrants, ...]

    def grants_at(self, level: int) -> LevelGrants:
        if not 1 <= level <= MAX_CLASS_LEVEL:
            raise KeyError(f"Class level must be 1 through {MAX_CLASS_LEVEL}.")
        return self.levels[level - 1]


@dataclass(frozen=True)
class ProgressionRegistry:
    version: int
    definitions: Mapping[str, ClassDefinition]
    features: Mapping[str, FeatureDefinition]
    resources: Mapping[str, ResourceProgression]
    spell_access: Mapping[str, SpellAccess]
    choices: Mapping[str, ChoiceSet]
    fingerprint: str

    def class_for(self, key: str) -> ClassDefinition:
        if not isinstance(key, str) or key not in self.definitions:
            raise RegistryValidationError(f"Unknown or unavailable class: {key}")
        return self.definitions[key]

    def is_available(self, key: object) -> bool:
        return isinstance(key, str) and key in self.definitions

    def chargen_summary(self, key: str) -> Mapping[str, Any]:
        definition = self.class_for(key)
        choice = self.choices[definition.skill_choice_key]
        return MappingProxyType(
            {
                "likes": definition.likes,
                "primary_ability": (
                    definition.primary_abilities[0]
                    if len(definition.primary_abilities) == 1
                    else list(definition.primary_abilities)
                ),
                "hit_die": definition.hit_die,
                "hp_base": definition.hit_die,
                "complexity": definition.complexity,
                "saving_throws": list(definition.saving_throws),
                "skill_choices": choice.count,
                "skills_available": list(choice.legal_options),
                "armor_training": list(definition.armor_training),
                "weapon_profs": definition.weapon_profs,
                "weapon_categories": list(definition.weapon_categories),
                "weapon_proficiencies": list(definition.weapon_proficiencies),
            }
        )

    def chargen_summaries(self) -> Mapping[str, Mapping[str, Any]]:
        return MappingProxyType(
            {key: self.chargen_summary(key) for key in self.definitions}
        )


def _index(values: Iterable[Any], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if not isinstance(value.key, str) or not value.key or value.key in result:
            raise RegistryValidationError(
                f"Duplicate or invalid {label} key: {value.key!r}"
            )
        result[value.key] = value
    return result


def _curve(values: tuple[int, ...], label: str) -> None:
    if len(values) != MAX_CLASS_LEVEL or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        raise RegistryValidationError(f"{label} must have three non-negative integers.")
    if any(right < left for left, right in zip(values, values[1:])):
        raise RegistryValidationError(f"{label} cannot decrease.")


def _reference(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("SRD 5.2.1 ")
        or len(value) > 160
    ):
        raise RegistryValidationError(f"{label} needs an SRD 5.2.1 reference.")


def build_registry(
    definitions: Iterable[ClassDefinition],
    features: Iterable[FeatureDefinition],
    resources: Iterable[ResourceProgression],
    spell_access: Iterable[SpellAccess],
    choices: Iterable[ChoiceSet],
    *,
    version: int = CLASS_REGISTRY_VERSION,
    available_owners: Iterable[str] = ("advancement", "combat", "magic", "resources"),
    required_help_keys: Iterable[str] = ("class progression",),
) -> ProgressionRegistry:
    """Validate and freeze the complete alpha dependency graph."""
    classes = _index(definitions, "class")
    feature_map = _index(features, "feature")
    resource_map = _index(resources, "resource")
    spell_map = _index(spell_access, "spell access")
    choice_map = _index(choices, "choice")
    if tuple(classes) != SELECTABLE_CLASS_NAMES:
        raise RegistryValidationError(
            "Selectable classes must be exactly the four alpha classes."
        )
    owners, help_keys = frozenset(available_owners), frozenset(required_help_keys)
    for feature in feature_map.values():
        if feature.owner not in owners or feature.help_key not in help_keys:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has no owner or help."
            )
        if feature.grant_mode not in {
            "automatic",
            "choice",
        } or feature.repeat_mode not in {"once", "repeat", "upgrade"}:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has invalid grant behavior."
            )
        if any(
            key not in feature_map or key == feature.key
            for key in feature.prerequisites
        ):
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an invalid prerequisite."
            )
        _reference(feature.srd_reference, f"Feature '{feature.key}'")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise RegistryValidationError("Feature prerequisites contain a cycle.")
        if key not in visited:
            visiting.add(key)
            for dependency in feature_map[key].prerequisites:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

    for key in feature_map:
        visit(key)
    for resource in resource_map.values():
        if resource.owner not in owners or resource.recovery_profile not in {
            "short_rest",
            "long_rest",
            "none",
        }:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has no owner or recovery profile."
            )
        _curve(resource.maxima, f"Resource '{resource.key}' maxima")
        _reference(resource.srd_reference, f"Resource '{resource.key}'")
    for access in spell_map.values():
        for field in (
            "cantrips",
            "spells_known",
            "spells_prepared",
            "spellbook_entries",
            "maximum_spell_level",
            "pact_slots",
            "pact_slot_level",
        ):
            _curve(getattr(access, field), f"Spell access '{access.key}' {field}")
        if len(access.spell_slots) != 9:
            raise RegistryValidationError(
                f"Spell access '{access.key}' needs nine slot columns."
            )
        for slots in access.spell_slots:
            _curve(slots, f"Spell access '{access.key}' slots")
        _reference(access.srd_reference, f"Spell access '{access.key}'")
    for choice in choice_map.values():
        if not 1 <= choice.count <= len(choice.legal_options) or len(
            set(choice.legal_options)
        ) != len(choice.legal_options):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has invalid options or count."
            )
        if (
            choice.replacement_policy not in {"none", "replace_one"}
            or choice.prerequisite_timing not in {"grant", "resolution"}
            or choice.option_adapter not in {"skill", "feature"}
        ):
            raise RegistryValidationError(f"Choice '{choice.key}' has invalid policy.")
        _reference(choice.srd_reference, f"Choice '{choice.key}'")
    for definition in classes.values():
        _reference(definition.srd_reference, f"Class '{definition.key}'")
        if (
            definition.hit_die not in {6, 8, 10}
            or definition.fixed_hp_gain != definition.hit_die // 2 + 1
        ):
            raise RegistryValidationError(
                f"Class '{definition.key}' has invalid HP progression."
            )
        if (
            definition.skill_choice_key not in choice_map
            or len(definition.saving_throws) != 2
        ):
            raise RegistryValidationError(
                f"Class '{definition.key}' has invalid core training."
            )
        if len(definition.levels) != 3 or tuple(
            item.level for item in definition.levels
        ) != (1, 2, 3):
            raise RegistryValidationError(
                f"Class '{definition.key}' must declare levels 1 through 3."
            )
        for level in definition.levels:
            for key, registry in (
                *((key, feature_map) for key in level.automatic_feature_keys),
                *((key, resource_map) for key in level.resource_keys),
                *((key, spell_map) for key in level.spell_access_keys),
                *((key, choice_map) for key in level.choice_keys),
            ):
                if key not in registry:
                    raise RegistryValidationError(
                        f"Class '{definition.key}' references unknown key '{key}'."
                    )
    payload = {
        "version": version,
        "classes": [asdict(v) for v in classes.values()],
        "features": [asdict(v) for v in feature_map.values()],
        "resources": [asdict(v) for v in resource_map.values()],
        "spells": [asdict(v) for v in spell_map.values()],
        "choices": [asdict(v) for v in choice_map.values()],
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return ProgressionRegistry(
        version,
        MappingProxyType(classes),
        MappingProxyType(feature_map),
        MappingProxyType(resource_map),
        MappingProxyType(spell_map),
        MappingProxyType(choice_map),
        fingerprint,
    )


def _feature(key: str, owner: str, page: int, *requirements: str) -> FeatureDefinition:
    label = key.rsplit(".", 1)[-1].replace("_", " ").title()
    return FeatureDefinition(
        key,
        owner,
        tuple(requirements),
        "automatic",
        "once",
        "class progression",
        f"SRD 5.2.1 p.{page}: {label}",
    )


def _default_registry() -> ProgressionRegistry:
    features = (
        _feature("cleric.spellcasting", "magic", 36),
        _feature("cleric.channel_divinity", "magic", 37),
        _feature("cleric.life_domain", "advancement", 40),
        _feature("cleric.disciple_of_life", "magic", 40, "cleric.life_domain"),
        _feature(
            "cleric.preserve_life",
            "magic",
            40,
            "cleric.life_domain",
            "cleric.channel_divinity",
        ),
        _feature("fighter.second_wind", "combat", 47),
        _feature("fighter.weapon_mastery", "combat", 47),
        _feature("fighter.action_surge", "combat", 47),
        _feature("fighter.tactical_mind", "combat", 47, "fighter.second_wind"),
        _feature("fighter.champion", "advancement", 49),
        _feature("fighter.improved_critical", "combat", 49, "fighter.champion"),
        _feature("fighter.remarkable_athlete", "combat", 49, "fighter.champion"),
        _feature("rogue.expertise", "advancement", 61),
        _feature("rogue.sneak_attack", "combat", 61),
        _feature("rogue.thieves_cant", "advancement", 62),
        _feature("rogue.weapon_mastery", "combat", 62),
        _feature("rogue.cunning_action", "combat", 62),
        _feature("rogue.thief", "advancement", 64),
        _feature("rogue.steady_aim", "combat", 62),
        _feature("rogue.fast_hands", "combat", 64, "rogue.thief"),
        _feature("rogue.second_story_work", "advancement", 64, "rogue.thief"),
        _feature("wizard.spellcasting", "magic", 77),
        _feature("wizard.ritual_adept", "magic", 78),
        _feature("wizard.arcane_recovery", "magic", 78),
        _feature("wizard.scholar", "advancement", 78),
        _feature("wizard.evoker", "advancement", 82),
        _feature("wizard.evocation_savant", "magic", 82, "wizard.evoker"),
        _feature("wizard.potent_cantrip", "magic", 82, "wizard.evoker"),
    )
    skills = {
        "Cleric": (2, ("History", "Insight", "Medicine", "Persuasion", "Religion"), 36),
        "Fighter": (
            2,
            (
                "Acrobatics",
                "Animal Handling",
                "Athletics",
                "History",
                "Insight",
                "Intimidation",
                "Perception",
                "Persuasion",
                "Survival",
            ),
            46,
        ),
        "Rogue": (
            4,
            (
                "Acrobatics",
                "Athletics",
                "Deception",
                "Insight",
                "Intimidation",
                "Investigation",
                "Perception",
                "Persuasion",
                "Sleight of Hand",
                "Stealth",
            ),
            61,
        ),
        "Wizard": (
            2,
            (
                "Arcana",
                "History",
                "Insight",
                "Investigation",
                "Medicine",
                "Nature",
                "Religion",
            ),
            77,
        ),
    }
    choices = [
        ChoiceSet(
            f"{name.lower()}.skills",
            count,
            options,
            "none",
            (),
            "grant",
            srd_reference=f"SRD 5.2.1 p.{page}: Core {name} Traits",
        )
        for name, (count, options, page) in skills.items()
    ]
    choices += [
        ChoiceSet(
            "cleric.divine_order",
            1,
            ("Protector", "Thaumaturge"),
            "none",
            (),
            "resolution",
            "feature",
            "SRD 5.2.1 p.37: Divine Order",
        ),
        ChoiceSet(
            "fighter.fighting_style",
            1,
            ("Archery", "Defense", "Great Weapon Fighting", "Two-Weapon Fighting"),
            "none",
            (),
            "resolution",
            "feature",
            "SRD 5.2.1 p.46: Fighting Style",
        ),
        ChoiceSet(
            "rogue.expertise",
            2,
            skills["Rogue"][1],
            "none",
            (),
            "resolution",
            "feature",
            "SRD 5.2.1 p.61: Expertise",
        ),
        ChoiceSet(
            "wizard.scholar",
            1,
            ("Arcana", "History", "Investigation", "Medicine", "Nature", "Religion"),
            "none",
            (),
            "resolution",
            "feature",
            "SRD 5.2.1 p.78: Scholar",
        ),
    ]
    resources = (
        ResourceProgression(
            "fighter.second_wind",
            "resources",
            (2, 2, 2),
            "short_rest",
            "SRD 5.2.1 p.47: Second Wind",
        ),
        ResourceProgression(
            "fighter.action_surge",
            "resources",
            (0, 1, 1),
            "short_rest",
            "SRD 5.2.1 p.47: Action Surge",
        ),
        ResourceProgression(
            "cleric.channel_divinity",
            "resources",
            (0, 2, 2),
            "short_rest",
            "SRD 5.2.1 p.37: Channel Divinity",
        ),
        ResourceProgression(
            "wizard.arcane_recovery",
            "resources",
            (1, 1, 1),
            "long_rest",
            "SRD 5.2.1 p.78: Arcane Recovery",
        ),
    )
    zero = (0, 0, 0)
    slots = ((2, 3, 4), (0, 0, 2), zero, zero, zero, zero, zero, zero, zero)
    spell_access = (
        SpellAccess(
            "cleric.spell_access",
            "Wisdom",
            (3, 3, 3),
            zero,
            (4, 5, 6),
            zero,
            (1, 1, 2),
            slots,
            zero,
            zero,
            "SRD 5.2.1 p.36: Cleric Features table",
            "table count",
        ),
        SpellAccess(
            "wizard.spell_access",
            "Intelligence",
            (3, 3, 3),
            zero,
            (4, 5, 6),
            (6, 8, 10),
            (1, 1, 2),
            slots,
            zero,
            zero,
            "SRD 5.2.1 p.77: Wizard Features table",
            "table count",
        ),
    )
    core = {
        "Cleric": (
            ("Wisdom",),
            "Wisdom",
            8,
            ("Wisdom", "Charisma"),
            ("Light", "Medium", "Shields"),
            ("simple",),
            (),
            "Simple weapons",
            "Gods",
            "High",
            36,
        ),
        "Fighter": (
            ("Strength", "Dexterity"),
            None,
            10,
            ("Strength", "Constitution"),
            ("Light", "Medium", "Heavy", "Shields"),
            ("simple", "martial"),
            (),
            "Simple and Martial weapons",
            "Battle",
            "Average",
            46,
        ),
        "Rogue": (
            ("Dexterity",),
            None,
            8,
            ("Dexterity", "Intelligence"),
            ("Light",),
            ("simple",),
            ("hand_crossbow", "rapier", "scimitar", "short_sword"),
            "Simple weapons and Martial weapons with Finesse or Light",
            "Stealth",
            "Low",
            61,
        ),
        "Wizard": (
            ("Intelligence",),
            "Intelligence",
            6,
            ("Intelligence", "Wisdom"),
            (),
            ("simple",),
            (),
            "Simple weapons",
            "Arcane study",
            "High",
            77,
        ),
    }
    levels = {
        "Cleric": (
            LevelGrants(
                1,
                ("cleric.spellcasting",),
                spell_access_keys=("cleric.spell_access",),
                choice_keys=("cleric.skills", "cleric.divine_order"),
            ),
            LevelGrants(
                2,
                ("cleric.channel_divinity",),
                ("cleric.channel_divinity",),
                ("cleric.spell_access",),
            ),
            LevelGrants(
                3,
                (
                    "cleric.life_domain",
                    "cleric.disciple_of_life",
                    "cleric.preserve_life",
                ),
                ("cleric.channel_divinity",),
                ("cleric.spell_access",),
            ),
        ),
        "Fighter": (
            LevelGrants(
                1,
                ("fighter.second_wind", "fighter.weapon_mastery"),
                ("fighter.second_wind",),
                choice_keys=("fighter.skills", "fighter.fighting_style"),
            ),
            LevelGrants(
                2,
                ("fighter.action_surge", "fighter.tactical_mind"),
                ("fighter.second_wind", "fighter.action_surge"),
            ),
            LevelGrants(
                3,
                (
                    "fighter.champion",
                    "fighter.improved_critical",
                    "fighter.remarkable_athlete",
                ),
                ("fighter.second_wind", "fighter.action_surge"),
            ),
        ),
        "Rogue": (
            LevelGrants(
                1,
                (
                    "rogue.expertise",
                    "rogue.sneak_attack",
                    "rogue.thieves_cant",
                    "rogue.weapon_mastery",
                ),
                choice_keys=("rogue.skills", "rogue.expertise"),
            ),
            LevelGrants(2, ("rogue.cunning_action",)),
            LevelGrants(
                3,
                (
                    "rogue.thief",
                    "rogue.steady_aim",
                    "rogue.fast_hands",
                    "rogue.second_story_work",
                ),
            ),
        ),
        "Wizard": (
            LevelGrants(
                1,
                (
                    "wizard.spellcasting",
                    "wizard.ritual_adept",
                    "wizard.arcane_recovery",
                ),
                ("wizard.arcane_recovery",),
                ("wizard.spell_access",),
                ("wizard.skills",),
            ),
            LevelGrants(
                2,
                ("wizard.scholar",),
                ("wizard.arcane_recovery",),
                ("wizard.spell_access",),
                ("wizard.scholar",),
            ),
            LevelGrants(
                3,
                ("wizard.evoker", "wizard.evocation_savant", "wizard.potent_cantrip"),
                ("wizard.arcane_recovery",),
                ("wizard.spell_access",),
            ),
        ),
    }
    definitions = []
    for name in SELECTABLE_CLASS_NAMES:
        (
            primary,
            casting,
            die,
            saves,
            armor,
            categories,
            weapons,
            prose,
            likes,
            complexity,
            page,
        ) = core[name]
        definitions.append(
            ClassDefinition(
                name,
                name,
                likes,
                primary,
                casting,
                die,
                die // 2 + 1,
                saves,
                armor,
                categories,
                weapons,
                prose,
                complexity,
                f"{name.lower()}.skills",
                f"SRD 5.2.1 p.{page}: Core {name} Traits",
                levels[name],
            )
        )
    return build_registry(definitions, features, resources, spell_access, choices)


CLASS_PROGRESSION = _default_registry()
CLASSES = CLASS_PROGRESSION.chargen_summaries()

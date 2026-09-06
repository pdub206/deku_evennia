"""Immutable, validated class-progression definitions (ADV-02).

Definitions in this module are data, not executable game behaviour.  Systems
refer to their stable keys and remain responsible for applying their own
effects.  This keeps Attributes safe to serialize and makes a registry version
useful for later reconciliation rather than silently changing existing PCs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Iterable, Mapping

MAX_CLASS_LEVEL = 20
CLASS_REGISTRY_VERSION = 1
SELECTABLE_CLASS_NAMES = (
    "Barbarian",
    "Bard",
    "Cleric",
    "Druid",
    "Fighter",
    "Monk",
    "Paladin",
    "Ranger",
    "Rogue",
    "Sorcerer",
    "Warlock",
    "Wizard",
)


class RegistryValidationError(ValueError):
    """Raised when class data cannot safely be offered to a player."""


@dataclass(frozen=True)
class FeatureDefinition:
    """A stable feature key and the system which owns its eventual effects."""

    key: str
    owner: str
    prerequisites: tuple[str, ...]
    grant_mode: str
    repeat_mode: str
    help_key: str


@dataclass(frozen=True)
class ResourceProgression:
    """A resource maximum and recovery policy, expressed only as primitives."""

    key: str
    owner: str
    maxima: tuple[int, ...]
    recovery_profile: str


@dataclass(frozen=True)
class SpellAccess:
    """One class's spell-access counts at each character level."""

    key: str
    casting_ability: str
    cantrips: tuple[int, ...]
    spells_known: tuple[int, ...]
    spells_prepared: tuple[int, ...]
    spellbook_entries: tuple[int, ...]
    maximum_spell_level: tuple[int, ...]
    preparation_formula: str | None = None


@dataclass(frozen=True)
class ChoiceSet:
    """A data-only entitlement ADV-03 can resolve without class-specific code."""

    key: str
    count: int
    legal_options: tuple[str, ...]
    replacement_policy: str
    mutual_exclusions: tuple[tuple[str, ...], ...]
    prerequisite_timing: str


@dataclass(frozen=True)
class LevelGrants:
    """The stable keys granted or made available at one level."""

    level: int
    automatic_feature_keys: tuple[str, ...] = ()
    resource_keys: tuple[str, ...] = ()
    spell_access_keys: tuple[str, ...] = ()
    choice_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassDefinition:
    """Complete immutable progression and chargen information for one class."""

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
    levels: tuple[LevelGrants, ...]

    def grants_at(self, level: int) -> LevelGrants:
        """Return one validated level's data without allowing partial lookup."""
        if not 1 <= level <= MAX_CLASS_LEVEL:
            raise KeyError(f"Class level must be 1 through {MAX_CLASS_LEVEL}.")
        return self.levels[level - 1]


@dataclass(frozen=True)
class ProgressionRegistry:
    """Read-only registry shared by chargen and progression consumers."""

    version: int
    definitions: Mapping[str, ClassDefinition]
    features: Mapping[str, FeatureDefinition]
    resources: Mapping[str, ResourceProgression]
    spell_access: Mapping[str, SpellAccess]
    choices: Mapping[str, ChoiceSet]
    fingerprint: str

    def class_for(self, key: str) -> ClassDefinition:
        """Look up a canonical class key or fail closed."""
        if not isinstance(key, str):
            raise RegistryValidationError(f"Unknown or unavailable class: {key!r}")
        try:
            return self.definitions[key]
        except KeyError as err:
            raise RegistryValidationError(
                f"Unknown or unavailable class: {key}"
            ) from err

    def is_available(self, key: object) -> bool:
        """Return whether a class passed the complete registry validation."""
        return isinstance(key, str) and key in self.definitions

    def chargen_summary(self, key: str) -> Mapping[str, Any]:
        """Project registry data into the legacy chargen presentation shape."""
        definition = self.class_for(key)
        choice = self.choices[definition.skill_choice_key]
        return MappingProxyType(
            {
                "likes": definition.likes,
                "primary_ability": _one_or_many(definition.primary_abilities),
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
        """Return source-ordered, immutable summaries for the legacy UI only."""
        return MappingProxyType(
            {key: self.chargen_summary(key) for key in self.definitions}
        )


def build_registry(
    definitions: Iterable[ClassDefinition],
    features: Iterable[FeatureDefinition],
    resources: Iterable[ResourceProgression],
    spell_access: Iterable[SpellAccess],
    choices: Iterable[ChoiceSet],
    *,
    version: int = CLASS_REGISTRY_VERSION,
    available_owners: Iterable[str] = ("advancement", "magic", "resources"),
    required_help_keys: Iterable[str] = ("class progression",),
) -> ProgressionRegistry:
    """Validate and freeze a complete class-progression graph.

    The function is intentionally public: tests and builder tooling can verify
    proposed definitions before a reload makes them selectable.
    """
    definitions_by_key = _indexed(definitions, "class")
    features_by_key = _indexed(features, "feature")
    resources_by_key = _indexed(resources, "resource")
    spells_by_key = _indexed(spell_access, "spell access")
    choices_by_key = _indexed(choices, "choice")
    owners = frozenset(available_owners)
    help_keys = frozenset(required_help_keys)

    if tuple(definitions_by_key) != SELECTABLE_CLASS_NAMES:
        raise RegistryValidationError(
            "Selectable classes must be exactly the twelve source-controlled classes."
        )
    for definition in definitions_by_key.values():
        _validate_class(
            definition, features_by_key, resources_by_key, spells_by_key, choices_by_key
        )
    for feature in features_by_key.values():
        if feature.owner not in owners:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has no owning adapter."
            )
        if feature.help_key not in help_keys:
            raise RegistryValidationError(
                f"Feature '{feature.key}' references missing help '{feature.help_key}'."
            )
        if feature.grant_mode not in {"automatic", "choice"}:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has invalid grant mode."
            )
        if feature.repeat_mode not in {"once", "repeat", "upgrade"}:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has invalid repeat mode."
            )
        if feature.key in feature.prerequisites:
            raise RegistryValidationError(
                f"Feature '{feature.key}' cannot require itself."
            )
        if any(
            prerequisite not in features_by_key
            for prerequisite in feature.prerequisites
        ):
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an unknown prerequisite."
            )
    _validate_feature_cycles(features_by_key)
    for resource in resources_by_key.values():
        if resource.owner not in owners or resource.recovery_profile not in {
            "short_rest",
            "long_rest",
            "none",
        }:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has an unavailable owner or recovery profile."
            )
        _validate_non_decreasing(resource.maxima, f"Resource '{resource.key}' maxima")
    for access in spells_by_key.values():
        _validate_spell_access(access)
    for choice in choices_by_key.values():
        if choice.count < 1 or choice.count > len(choice.legal_options):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an impossible count."
            )
        if len(set(choice.legal_options)) != len(choice.legal_options):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has duplicate legal options."
            )
        if choice.replacement_policy not in {"none", "replace_one"}:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an invalid replacement policy."
            )
        if choice.prerequisite_timing not in {"grant", "resolution"}:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has invalid prerequisite timing."
            )

    payload = {
        "version": version,
        "definitions": [asdict(value) for value in definitions_by_key.values()],
        "features": [asdict(value) for value in features_by_key.values()],
        "resources": [asdict(value) for value in resources_by_key.values()],
        "spell_access": [asdict(value) for value in spells_by_key.values()],
        "choices": [asdict(value) for value in choices_by_key.values()],
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return ProgressionRegistry(
        version,
        MappingProxyType(definitions_by_key),
        MappingProxyType(features_by_key),
        MappingProxyType(resources_by_key),
        MappingProxyType(spells_by_key),
        MappingProxyType(choices_by_key),
        fingerprint,
    )


def _indexed(values: Iterable[Any], label: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for value in values:
        if not isinstance(value.key, str) or not value.key or value.key in indexed:
            raise RegistryValidationError(
                f"Duplicate or invalid {label} key: {value.key!r}"
            )
        indexed[value.key] = value
    return indexed


def _validate_class(
    definition: ClassDefinition,
    features: Mapping[str, FeatureDefinition],
    resources: Mapping[str, ResourceProgression],
    spells: Mapping[str, SpellAccess],
    choices: Mapping[str, ChoiceSet],
) -> None:
    if (
        definition.hit_die not in {6, 8, 10, 12}
        or definition.fixed_hp_gain != definition.hit_die // 2 + 1
    ):
        raise RegistryValidationError(
            f"Class '{definition.key}' has invalid hit-die HP progression."
        )
    if len(definition.saving_throws) != 2 or len(definition.primary_abilities) not in {
        1,
        2,
    }:
        raise RegistryValidationError(
            f"Class '{definition.key}' has invalid core abilities."
        )
    if definition.skill_choice_key not in choices:
        raise RegistryValidationError(
            f"Class '{definition.key}' has no valid skill choice set."
        )
    if len(definition.levels) != MAX_CLASS_LEVEL or tuple(
        level.level for level in definition.levels
    ) != tuple(range(1, MAX_CLASS_LEVEL + 1)):
        raise RegistryValidationError(
            f"Class '{definition.key}' must declare levels 1 through 20."
        )
    for grants in definition.levels:
        for key in grants.automatic_feature_keys:
            if key not in features:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown feature '{key}'."
                )
        for key in grants.resource_keys:
            if key not in resources:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown resource '{key}'."
                )
        for key in grants.spell_access_keys:
            if key not in spells:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown spell access '{key}'."
                )
        for key in grants.choice_keys:
            if key not in choices:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown choice '{key}'."
                )


def _validate_feature_cycles(features: Mapping[str, FeatureDefinition]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise RegistryValidationError("Feature prerequisites contain a cycle.")
        if key in visited:
            return
        visiting.add(key)
        for prerequisite in features[key].prerequisites:
            visit(prerequisite)
        visiting.remove(key)
        visited.add(key)

    for key in features:
        visit(key)


def _validate_non_decreasing(values: tuple[int, ...], label: str) -> None:
    if len(values) != MAX_CLASS_LEVEL or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        raise RegistryValidationError(
            f"{label} must have twenty non-negative integer entries."
        )
    if any(after < before for before, after in zip(values, values[1:])):
        raise RegistryValidationError(f"{label} cannot decrease.")


def _validate_spell_access(access: SpellAccess) -> None:
    for name in (
        "cantrips",
        "spells_known",
        "spells_prepared",
        "spellbook_entries",
        "maximum_spell_level",
    ):
        values = getattr(access, name)
        _validate_non_decreasing(values, f"Spell access '{access.key}' {name}")
    if any(value > 9 for value in access.maximum_spell_level):
        raise RegistryValidationError(
            f"Spell access '{access.key}' exceeds ninth-level spells."
        )


def _one_or_many(values: tuple[str, ...]) -> str | list[str]:
    return values[0] if len(values) == 1 else list(values)


def _spell_levels(kind: str) -> tuple[int, ...]:
    if kind == "full":
        thresholds = (1, 3, 5, 7, 9, 11, 13, 15, 17)
    elif kind == "half":
        thresholds = (2, 5, 9, 13, 17)
    elif kind == "pact":
        thresholds = (1, 3, 5, 7, 9)
    else:
        return (0,) * MAX_CLASS_LEVEL
    return tuple(
        sum(level >= threshold for threshold in thresholds) for level in range(1, 21)
    )


def _cantrips(kind: str) -> tuple[int, ...]:
    if kind == "none":
        return (0,) * MAX_CLASS_LEVEL
    return tuple(2 + (level >= 4) + (level >= 10) for level in range(1, 21))


def _default_registry() -> ProgressionRegistry:
    # Kept private in chargen_data so presentation has no competing public
    # class source.  The registry is the sole public authoritative interface.
    from world.chargen_data import _CLASS_SUMMARIES

    caster_kinds = {
        "Bard": "full",
        "Cleric": "full",
        "Druid": "full",
        "Sorcerer": "full",
        "Wizard": "full",
        "Paladin": "half",
        "Ranger": "half",
        "Warlock": "pact",
    }
    resources_by_class = {
        "Barbarian": ("rage", "long_rest"),
        "Bard": ("bardic_inspiration", "long_rest"),
        "Cleric": ("channel_divinity", "short_rest"),
        "Druid": ("wild_shape", "short_rest"),
        "Fighter": ("second_wind", "short_rest"),
        "Monk": ("focus", "short_rest"),
        "Paladin": ("lay_on_hands", "long_rest"),
        "Ranger": (),
        "Rogue": ("cunning_strike", "none"),
        "Sorcerer": ("sorcery_points", "long_rest"),
        "Warlock": ("pact_magic", "short_rest"),
        "Wizard": ("arcane_recovery", "long_rest"),
    }
    features: list[FeatureDefinition] = []
    resources: list[ResourceProgression] = []
    spell_access: list[SpellAccess] = []
    choices: list[ChoiceSet] = []
    definitions: list[ClassDefinition] = []
    for name in SELECTABLE_CLASS_NAMES:
        summary = _CLASS_SUMMARIES[name]
        key = name.lower()
        skill_choice_key = f"{key}.skills"
        choices.append(
            ChoiceSet(
                skill_choice_key,
                summary["skill_choices"],
                tuple(summary["skills_available"]),
                "none",
                (),
                "grant",
            )
        )
        feature_key = f"{key}.class_features"
        features.append(
            FeatureDefinition(
                feature_key,
                "advancement",
                (),
                "automatic",
                "upgrade",
                "class progression",
            )
        )
        resource_keys: tuple[str, ...] = ()
        if resources_by_class[name]:
            resource_name, recovery = resources_by_class[name]
            resource_key = f"{key}.{resource_name}"
            # A data-only capacity curve; the owning system decides how a use is spent.
            maxima = tuple(1 + (level - 1) // 4 for level in range(1, 21))
            resources.append(
                ResourceProgression(resource_key, "resources", maxima, recovery)
            )
            resource_keys = (resource_key,)
        spell_keys: tuple[str, ...] = ()
        if name in caster_kinds:
            kind = caster_kinds[name]
            spell_key = f"{key}.spell_access"
            maximum = _spell_levels(kind)
            spell_access.append(
                SpellAccess(
                    spell_key,
                    str(
                        summary["primary_ability"]
                        if isinstance(summary["primary_ability"], str)
                        else summary["primary_ability"][-1]
                    ),
                    _cantrips("none" if name in {"Paladin", "Ranger"} else kind),
                    (0,) * MAX_CLASS_LEVEL,
                    (
                        tuple(maximum[level - 1] for level in range(1, 21))
                        if name in {"Cleric", "Druid", "Paladin", "Ranger"}
                        else (0,) * MAX_CLASS_LEVEL
                    ),
                    (
                        tuple(6 + 2 * (level - 1) for level in range(1, 21))
                        if name == "Wizard"
                        else (0,) * MAX_CLASS_LEVEL
                    ),
                    maximum,
                    (
                        "casting_ability_modifier + class_level"
                        if name in {"Cleric", "Druid", "Paladin", "Ranger"}
                        else None
                    ),
                )
            )
            spell_keys = (spell_key,)
        levels = tuple(
            LevelGrants(
                level,
                (feature_key,),
                resource_keys,
                spell_keys,
                (skill_choice_key,) if level == 1 else (),
            )
            for level in range(1, 21)
        )
        primary = summary["primary_ability"]
        definitions.append(
            ClassDefinition(
                name,
                name,
                summary["likes"],
                (primary,) if isinstance(primary, str) else tuple(primary),
                (
                    primary
                    if isinstance(primary, str) and name in caster_kinds
                    else (primary[-1] if name in caster_kinds else None)
                ),
                summary["hit_die"],
                summary["hit_die"] // 2 + 1,
                tuple(summary["saving_throws"]),
                tuple(summary["armor_training"]),
                tuple(summary["weapon_categories"]),
                tuple(summary["weapon_proficiencies"]),
                summary["weapon_profs"],
                summary["complexity"],
                skill_choice_key,
                levels,
            )
        )
    return build_registry(definitions, features, resources, spell_access, choices)


CLASS_PROGRESSION = _default_registry()
CLASSES = CLASS_PROGRESSION.chargen_summaries()

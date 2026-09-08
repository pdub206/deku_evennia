"""Immutable, validated class-progression definitions (ADV-02).

Definitions in this module are data, not executable game behaviour.  Systems
refer to their stable keys and remain responsible for applying their own
effects.  This keeps Attributes safe to serialize and makes a registry version
useful for later reconciliation rather than silently changing existing PCs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Iterable, Mapping

MAX_CLASS_LEVEL = 20
# Version 10 adds durable occurrence provenance for released progression.
CLASS_REGISTRY_VERSION = 10
MAX_SRD_REFERENCE_LENGTH = 160
SRD_REFERENCE_PREFIX = "SRD 5.2.1 "
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
    """A cited feature identity and the adapter that may eventually release it.

    A catalogued feature is source data only. It cannot be placed in an
    automatic grant until a code-owned adapter has promoted it to ``released``.
    This keeps the progression table truthful without treating an SRD name as
    implemented player-facing mechanics.
    """

    key: str
    owner: str
    prerequisites: tuple[str, ...]
    grant_mode: str
    repeat_mode: str
    help_key: str
    display_name: str
    srd_reference: str
    release_state: str = "catalogued"
    feature_shape: str = "unclassified"
    release_adapter: str = ""
    action_key: str = ""


@dataclass(frozen=True)
class ResourceProgression:
    """A cited non-spell resource record that remains unavailable until released."""

    key: str
    owner: str
    maxima: tuple[int, ...]
    recovery_profile: str
    display_name: str
    srd_reference: str
    spend_profile: str
    capacity_expression: str = ""
    release_state: str = "catalogued"


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
    spell_slots: tuple[tuple[int, ...], ...]
    pact_slots: tuple[int, ...]
    pact_slot_level: tuple[int, ...]
    srd_reference: str
    preparation_formula: str | None = None
    preparation_timing: str = "long_rest"


@dataclass(frozen=True)
class ChoiceSet:
    """A data-only entitlement ADV-03 can resolve without class-specific code.

    ``option_adapter`` identifies the narrow system that owns the selected
    stable keys.  It is deliberately not a callable or import path: class
    progression remains declarative and the adapter enforces its own rules.
    """

    key: str
    count: int
    legal_options: tuple[str, ...]
    replacement_policy: str
    mutual_exclusions: tuple[tuple[str, ...], ...]
    prerequisite_timing: str
    option_adapter: str = "skill"
    srd_reference: str = ""
    release_state: str = "released"


@dataclass(frozen=True)
class LevelGrants:
    """The released grants and cited-but-unreleased entries at one level."""

    level: int
    automatic_feature_keys: tuple[str, ...] = ()
    resource_keys: tuple[str, ...] = ()
    spell_access_keys: tuple[str, ...] = ()
    choice_keys: tuple[str, ...] = ()
    catalogued_feature_keys: tuple[str, ...] = ()
    catalogued_subclass_choice_keys: tuple[str, ...] = ()
    catalogued_subclass_feature_keys: tuple[str, ...] = ()


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
    srd_reference: str
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
        """Return whether a class is present in the validated source registry.

        Presence is intentionally weaker than implementation and publication.
        Consumers that need player availability must ask the release manifest;
        this legacy lookup remains for authored-data and repair tooling.
        """
        return isinstance(key, str) and key in self.definitions

    def implementation_blockers(self, key: str, level: int) -> tuple[str, ...]:
        """Return deterministic reasons a class level lacks real adapters.

        This is the M3-F02 boundary between a name in the SRD catalogue and a
        mechanically implemented progression coordinate.  It never claims
        publication; P-05 combines this output with real action/help/effect
        evidence before exposing a class to players.
        """
        definition = self.class_for(key)
        if (
            isinstance(level, bool)
            or not isinstance(level, int)
            or not 1 <= level <= MAX_CLASS_LEVEL
        ):
            raise RegistryValidationError("Class level must be 1 through 20.")
        blockers: list[str] = []
        for grants in definition.levels[:level]:
            blockers.extend(
                f"catalogued_feature:{feature_key}"
                for feature_key in grants.catalogued_feature_keys
            )
            blockers.extend(
                f"catalogued_subclass_choice:{feature_key}"
                for feature_key in grants.catalogued_subclass_choice_keys
            )
            blockers.extend(
                f"catalogued_subclass_feature:{feature_key}"
                for feature_key in grants.catalogued_subclass_feature_keys
            )
            blockers.extend(
                f"catalogued_resource:{resource_key}"
                for resource_key in grants.resource_keys
                if self.resources[resource_key].release_state != "released"
            )
            blockers.extend(
                f"catalogued_choice:{choice_key}"
                for choice_key in grants.choice_keys
                if self.choices[choice_key].release_state != "released"
            )
        return tuple(dict.fromkeys(blockers))

    def is_implemented(self, key: str, level: int) -> bool:
        """Return whether this coordinate has no catalogue-only progression data."""
        return not self.implementation_blockers(key, level)

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
    available_owners: Iterable[str] = (
        "advancement",
        "character_stats",
        "catalogue",
        "magic",
        "resources",
    ),
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
        if (
            not isinstance(feature.display_name, str)
            or not feature.display_name.strip()
        ):
            raise RegistryValidationError(
                f"Feature '{feature.key}' needs a player-safe display name."
            )
        _validate_srd_reference(feature.srd_reference, f"Feature '{feature.key}'")
        if feature.release_state not in {"catalogued", "released"}:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an invalid release state."
            )
        if feature.release_state == "catalogued" and feature.owner != "catalogue":
            raise RegistryValidationError(
                f"Catalogued feature '{feature.key}' cannot claim an action adapter."
            )
        if feature.release_state == "released" and feature.owner == "catalogue":
            raise RegistryValidationError(
                f"Released feature '{feature.key}' needs an owning adapter."
            )
        if not isinstance(feature.action_key, str) or (
            feature.action_key and not _valid_stable_key(feature.action_key)
        ):
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an invalid action key."
            )
        if feature.release_state == "catalogued" and feature.action_key:
            raise RegistryValidationError(
                f"Catalogued feature '{feature.key}' cannot expose an action."
            )
        if feature.feature_shape not in {
            "unclassified",
            "passive",
            "active",
            "resource",
            "choice",
        }:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an invalid implementation shape."
            )
        if feature.release_adapter not in {
            "advancement.choice",
            "advancement.passive",
            "character_stats.unarmored_defense",
            "combat.class_feature",
            "magic.class_feature",
            "resources.class_feature",
            "subclass.feature",
        }:
            raise RegistryValidationError(
                f"Feature '{feature.key}' has an invalid release adapter."
            )
        if (
            feature.release_state == "released"
            and feature.feature_shape == "unclassified"
        ):
            raise RegistryValidationError(
                f"Released feature '{feature.key}' needs an implementation shape."
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
        if resource.owner not in owners:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has an unavailable owner."
            )
        _validate_srd_reference(resource.srd_reference, f"Resource '{resource.key}'")
        if resource.release_state not in {"catalogued", "released"}:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has an invalid release state."
            )
        if resource.release_state == "catalogued" and resource.owner != "catalogue":
            raise RegistryValidationError(
                f"Catalogued resource '{resource.key}' cannot claim an action adapter."
            )
        if resource.release_state == "released" and resource.owner == "catalogue":
            raise RegistryValidationError(
                f"Released resource '{resource.key}' needs an owning adapter."
            )
        if resource.recovery_profile not in {
            "short_rest_one_long_rest_full",
            "long_rest_full_until_level_4; short_or_long_rest_full_from_level_5",
            "short_or_long_rest_full",
            "long_rest_full",
            "per_eligible_attack",
        }:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has an unavailable recovery profile."
            )
        if resource.spend_profile not in {
            "one_use_per_rage",
            "one_die_per_inspiration",
            "one_use_per_channel_divinity_effect",
            "one_use_per_transformation",
            "one_use_per_second_wind_or_tactical_mind",
            "feature_declared_focus_cost",
            "one_or_more_points_per_healing_or_cure",
            "one_free_hunters_mark_cast",
            "forfeit_sneak_attack_dice_on_hit",
            "feature_declared_sorcery_point_cost",
            "one_short_rest_use_to_recover_slots_under_level_6_totaling_half_wizard_level_rounded_up",
        }:
            raise RegistryValidationError(
                f"Resource '{resource.key}' has an unavailable spend profile."
            )
        if resource.maxima:
            _validate_non_decreasing(
                resource.maxima, f"Resource '{resource.key}' maxima"
            )
            if resource.capacity_expression:
                raise RegistryValidationError(
                    f"Resource '{resource.key}' cannot have two capacity definitions."
                )
        elif resource.capacity_expression not in {
            "max(1, Charisma modifier)",
        }:
            raise RegistryValidationError(
                f"Resource '{resource.key}' needs a complete capacity definition."
            )
        if (
            not isinstance(resource.display_name, str)
            or not resource.display_name.strip()
        ):
            raise RegistryValidationError(
                f"Resource '{resource.key}' needs a player-safe display name."
            )
    for access in spells_by_key.values():
        _validate_spell_access(access)
    for choice in choices_by_key.values():
        _validate_srd_reference(choice.srd_reference, f"Choice '{choice.key}'")
        if choice.release_state not in {"catalogued", "released"}:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an invalid release state."
            )
        if choice.count < 1 or choice.count > len(choice.legal_options):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an impossible count."
            )
        if len(set(choice.legal_options)) != len(choice.legal_options):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has duplicate legal options."
            )
        if any(
            not isinstance(option, str) or not option.strip()
            for option in choice.legal_options
        ):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an invalid legal option."
            )
        if choice.replacement_policy not in {"none", "replace_one"}:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an invalid replacement policy."
            )
        if choice.prerequisite_timing not in {"grant", "resolution"}:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has invalid prerequisite timing."
            )
        if choice.option_adapter not in {
            "skill",
            "magic_learned",
            "magic_prepared",
            "magic_spellbook",
            "magic_innate",
        }:
            raise RegistryValidationError(
                f"Choice '{choice.key}' has an unavailable option adapter."
            )
        if (
            choice.replacement_policy == "replace_one"
            and choice.option_adapter != "magic_learned"
        ):
            raise RegistryValidationError(
                f"Choice '{choice.key}' has no safe replacement adapter."
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
    _validate_srd_reference(definition.srd_reference, f"Class '{definition.key}'")
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
            if features[key].release_state != "released":
                raise RegistryValidationError(
                    f"Class '{definition.key}' cannot grant unreleased feature '{key}'."
                )
        for key in grants.resource_keys:
            if key not in resources:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown resource '{key}'."
                )
            if resources[key].release_state != "released":
                raise RegistryValidationError(
                    f"Class '{definition.key}' cannot grant catalogued resource '{key}'."
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
            if choices[key].release_state != "released":
                raise RegistryValidationError(
                    f"Class '{definition.key}' cannot grant catalogued choice '{key}'."
                )
        for key in grants.catalogued_feature_keys:
            if key not in features:
                raise RegistryValidationError(
                    f"Class '{definition.key}' references unknown catalogued feature '{key}'."
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


def _validate_srd_reference(value: object, label: str) -> None:
    """Require a concise, reviewable citation for source-controlled data."""
    if (
        not isinstance(value, str)
        or not value.startswith(SRD_REFERENCE_PREFIX)
        or len(value) > MAX_SRD_REFERENCE_LENGTH
    ):
        raise RegistryValidationError(
            f"{label} needs an SRD 5.2.1 section or table reference."
        )


def _valid_stable_key(value: str) -> bool:
    """Accept a compact, non-executable key shared with the action registry."""
    return bool(re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", value))


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
    if len(access.spell_slots) != 9:
        raise RegistryValidationError(
            f"Spell access '{access.key}' needs nine spell-slot columns."
        )
    for spell_level, slots in enumerate(access.spell_slots, start=1):
        _validate_non_decreasing(
            slots, f"Spell access '{access.key}' level-{spell_level} slots"
        )
        if any(value > 4 for value in slots):
            raise RegistryValidationError(
                f"Spell access '{access.key}' has an invalid slot maximum."
            )
    _validate_non_decreasing(
        access.pact_slots, f"Spell access '{access.key}' pact slots"
    )
    _validate_non_decreasing(
        access.pact_slot_level, f"Spell access '{access.key}' pact slot level"
    )
    if any(value > 5 for value in access.pact_slot_level):
        raise RegistryValidationError(
            f"Spell access '{access.key}' exceeds fifth-level Pact Magic slots."
        )
    if access.preparation_timing not in {"long_rest"}:
        raise RegistryValidationError(
            f"Spell access '{access.key}' has an unavailable preparation timing."
        )
    _validate_srd_reference(access.srd_reference, f"Spell access '{access.key}'")


def _one_or_many(values: tuple[str, ...]) -> str | list[str]:
    return values[0] if len(values) == 1 else list(values)


def _catalogued_srd_features() -> (
    tuple[list[FeatureDefinition], Mapping[str, tuple[tuple[str, ...], ...]]]
):
    """Project cited SRD feature tables into stable, non-released registry keys.

    The source table deliberately remains separate from game behaviour. These
    entries establish the immutable identity and exact level at which a future
    adapter must promote a feature; they do not create a player entitlement.
    """
    from systems.srd_class_features import (SRD_CLASS_FEATURES,
                                            classify_srd_feature)

    definitions: list[FeatureDefinition] = []
    keys_by_class: dict[str, tuple[tuple[str, ...], ...]] = {}
    for class_name, table in SRD_CLASS_FEATURES.items():
        class_key = class_name.casefold()
        labels = tuple(label for row in table.level_features for label in row)
        counts = {label: labels.count(label) for label in set(labels)}
        keys_by_label: dict[str, str] = {}
        for label in dict.fromkeys(labels):
            classification = classify_srd_feature(label)
            feature_key = f"{class_key}.{_catalogue_slug(label)}"
            keys_by_label[label] = feature_key
            definitions.append(
                FeatureDefinition(
                    feature_key,
                    "catalogue",
                    (),
                    "automatic",
                    "repeat" if counts[label] > 1 else "once",
                    "class progression",
                    label,
                    table.srd_reference,
                    feature_shape=classification.feature_shape,
                    release_adapter=classification.release_adapter,
                )
            )
        keys_by_class[class_name] = tuple(
            tuple(keys_by_label[label] for label in row) for row in table.level_features
        )
    return definitions, MappingProxyType(keys_by_class)


def _catalogued_srd_subclasses() -> tuple[
    list[FeatureDefinition],
    Mapping[str, tuple[str, tuple[tuple[str, ...], ...]]],
]:
    """Project SRD subclass selections and features without releasing them.

    The stored prerequisite means a future subclass resolver must select the
    named subclass before its feature adapter can grant anything.
    """
    from systems.srd_class_features import (SRD_SUBCLASS_FEATURES,
                                            classify_srd_feature)

    definitions: list[FeatureDefinition] = []
    records: dict[str, tuple[str, tuple[tuple[str, ...], ...]]] = {}
    for class_name, table in SRD_SUBCLASS_FEATURES.items():
        class_key = class_name.casefold()
        choice_key = f"{class_key}.{_catalogue_slug(table.subclass_name)}"
        definitions.append(
            FeatureDefinition(
                choice_key,
                "catalogue",
                (),
                "choice",
                "once",
                "class progression",
                table.subclass_name,
                table.srd_reference,
                feature_shape="choice",
                release_adapter="advancement.choice",
            )
        )
        labels = tuple(label for row in table.level_features for label in row)
        counts = {label: labels.count(label) for label in set(labels)}
        keys_by_label: dict[str, str] = {}
        for label in dict.fromkeys(labels):
            classification = classify_srd_feature(label)
            feature_key = f"{choice_key}.{_catalogue_slug(label)}"
            keys_by_label[label] = feature_key
            definitions.append(
                FeatureDefinition(
                    feature_key,
                    "catalogue",
                    (choice_key,),
                    "automatic",
                    "repeat" if counts[label] > 1 else "once",
                    "class progression",
                    label,
                    table.srd_reference,
                    feature_shape=classification.feature_shape,
                    release_adapter=classification.release_adapter,
                )
            )
        records[class_name] = (
            choice_key,
            tuple(
                tuple(keys_by_label[label] for label in row)
                for row in table.level_features
            ),
        )
    return definitions, MappingProxyType(records)


def _catalogue_slug(value: str) -> str:
    """Return a stable ASCII key component for an SRD-provided display name."""
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


# SRD 5.2.1 full-caster tables (Bard, Cleric, Druid, Sorcerer, Wizard),
# normalized as one row per character level and nine columns for spell levels.
_FULL_SPELL_SLOTS = (
    (2, 0, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 0, 0, 0, 0, 0, 0, 0),
    (4, 2, 0, 0, 0, 0, 0, 0, 0),
    (4, 3, 0, 0, 0, 0, 0, 0, 0),
    (4, 3, 2, 0, 0, 0, 0, 0, 0),
    (4, 3, 3, 0, 0, 0, 0, 0, 0),
    (4, 3, 3, 1, 0, 0, 0, 0, 0),
    (4, 3, 3, 2, 0, 0, 0, 0, 0),
    (4, 3, 3, 3, 1, 0, 0, 0, 0),
    (4, 3, 3, 3, 2, 0, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 0, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 0, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 0),
    (4, 3, 3, 3, 2, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 1, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 1, 1, 1),
    (4, 3, 3, 3, 3, 2, 2, 1, 1),
)
# SRD 5.2.1 Paladin and Ranger tables, likewise one row per character level.
_HALF_SPELL_SLOTS = (
    (2, 0, 0, 0, 0),
    (2, 0, 0, 0, 0),
    (3, 0, 0, 0, 0),
    (3, 0, 0, 0, 0),
    (4, 2, 0, 0, 0),
    (4, 2, 0, 0, 0),
    (4, 3, 0, 0, 0),
    (4, 3, 0, 0, 0),
    (4, 3, 2, 0, 0),
    (4, 3, 2, 0, 0),
    (4, 3, 3, 0, 0),
    (4, 3, 3, 0, 0),
    (4, 3, 3, 1, 0),
    (4, 3, 3, 1, 0),
    (4, 3, 3, 2, 0),
    (4, 3, 3, 2, 0),
    (4, 3, 3, 3, 1),
    (4, 3, 3, 3, 1),
    (4, 3, 3, 3, 2),
    (4, 3, 3, 3, 2),
)
_NO_SPELL_SLOTS = tuple((0,) * 9 for _ in range(MAX_CLASS_LEVEL))
_NO_PACT_SLOTS = (0,) * MAX_CLASS_LEVEL


def _slot_columns(
    rows: tuple[tuple[int, ...], ...], *, slot_levels: int
) -> tuple[tuple[int, ...], ...]:
    """Transpose source-table rows into stable level-keyed slot columns."""
    return tuple(
        tuple(row[spell_level] if spell_level < slot_levels else 0 for row in rows)
        for spell_level in range(9)
    )


_FULL_SLOT_COLUMNS = _slot_columns(_FULL_SPELL_SLOTS, slot_levels=9)
_HALF_SLOT_COLUMNS = _slot_columns(_HALF_SPELL_SLOTS, slot_levels=5)


def _default_registry() -> ProgressionRegistry:
    # Kept private in chargen_data so presentation has no competing public
    # class source.  The registry is the sole public authoritative interface.
    from systems.srd_class_resources import SRD_CLASS_RESOURCES
    from world.chargen_data import _CLASS_SUMMARIES

    class_references = {
        "Barbarian": "SRD 5.2.1 p.28: Barbarian Features table",
        "Bard": "SRD 5.2.1 p.31: Bard Features table",
        "Cleric": "SRD 5.2.1 p.36: Cleric Features table",
        "Druid": "SRD 5.2.1 p.41: Druid Features table",
        "Fighter": "SRD 5.2.1 p.46: Fighter Features table",
        "Monk": "SRD 5.2.1 p.50: Monk Features table",
        "Paladin": "SRD 5.2.1 p.53: Paladin Features table",
        "Ranger": "SRD 5.2.1 p.56: Ranger Features table",
        "Rogue": "SRD 5.2.1 p.61: Rogue Features table",
        "Sorcerer": "SRD 5.2.1 p.64: Sorcerer Features table",
        "Warlock": "SRD 5.2.1 p.70: Warlock Features table",
        "Wizard": "SRD 5.2.1 p.77: Wizard Features table",
    }
    full_maximum_spell_level = (
        1,
        1,
        2,
        2,
        3,
        3,
        4,
        4,
        5,
        5,
        6,
        6,
        7,
        7,
        8,
        8,
        9,
        9,
        9,
        9,
    )
    half_maximum_spell_level = (
        1,
        1,
        1,
        1,
        2,
        2,
        2,
        2,
        3,
        3,
        3,
        3,
        4,
        4,
        4,
        4,
        5,
        5,
        5,
        5,
    )
    prepared_full = (
        4,
        5,
        6,
        7,
        9,
        10,
        11,
        12,
        14,
        15,
        16,
        16,
        17,
        17,
        18,
        18,
        19,
        20,
        21,
        22,
    )
    prepared_half = (
        2,
        3,
        4,
        5,
        6,
        6,
        7,
        7,
        9,
        9,
        10,
        10,
        11,
        11,
        12,
        12,
        14,
        14,
        15,
        15,
    )
    spell_access_data = {
        "Bard": {
            "cantrips": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
            "prepared": prepared_full,
            "maximum": full_maximum_spell_level,
            "slots": _FULL_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.31: Bard Features table",
        },
        "Cleric": {
            "cantrips": (3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
            "prepared": prepared_full,
            "maximum": full_maximum_spell_level,
            "slots": _FULL_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.36: Cleric Features table",
        },
        "Druid": {
            "cantrips": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
            "prepared": prepared_full,
            "maximum": full_maximum_spell_level,
            "slots": _FULL_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.41: Druid Features table",
        },
        "Paladin": {
            "cantrips": (0,) * MAX_CLASS_LEVEL,
            "prepared": prepared_half,
            "maximum": half_maximum_spell_level,
            "slots": _HALF_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.53: Paladin Features table",
        },
        "Ranger": {
            "cantrips": (0,) * MAX_CLASS_LEVEL,
            "prepared": prepared_half,
            "maximum": half_maximum_spell_level,
            "slots": _HALF_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.57: Ranger Features table",
        },
        "Sorcerer": {
            "cantrips": (4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6),
            "prepared": (
                2,
                4,
                6,
                7,
                9,
                10,
                11,
                12,
                14,
                15,
                16,
                16,
                17,
                17,
                18,
                18,
                19,
                20,
                21,
                22,
            ),
            "maximum": full_maximum_spell_level,
            "slots": _FULL_SLOT_COLUMNS,
            "reference": "SRD 5.2.1 p.64: Sorcerer Features table",
        },
        "Warlock": {
            "cantrips": (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
            "prepared": (
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
                10,
                11,
                11,
                12,
                12,
                13,
                13,
                14,
                14,
                15,
                15,
            ),
            "maximum": full_maximum_spell_level,
            "slots": _slot_columns(_NO_SPELL_SLOTS, slot_levels=9),
            "pact_slots": (1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4),
            "pact_levels": (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
            "reference": "SRD 5.2.1 p.70: Warlock Features table",
        },
        "Wizard": {
            "cantrips": (3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5),
            "prepared": (
                4,
                5,
                6,
                7,
                9,
                10,
                11,
                12,
                14,
                15,
                16,
                16,
                17,
                18,
                19,
                21,
                22,
                23,
                24,
                25,
            ),
            "maximum": full_maximum_spell_level,
            "slots": _FULL_SLOT_COLUMNS,
            "spellbook_entries": tuple(6 + 2 * (level - 1) for level in range(1, 21)),
            "reference": "SRD 5.2.1 p.77: Wizard Features table",
        },
    }
    features, catalogued_feature_keys = _catalogued_srd_features()
    subclass_features, catalogued_subclasses = _catalogued_srd_subclasses()
    features.extend(subclass_features)
    released_feature_adapters = {
        "barbarian.unarmored_defense": (
            "character_stats",
            "character_stats.unarmored_defense",
            "",
        ),
        "fighter.second_wind": (
            "resources",
            "resources.class_feature",
            "fighter.second_wind",
        ),
        "monk.unarmored_defense": (
            "character_stats",
            "character_stats.unarmored_defense",
            "",
        ),
    }
    features = [
        (
            replace(
                feature,
                owner=released_feature_adapters[feature.key][0],
                release_state="released",
                release_adapter=released_feature_adapters[feature.key][1],
                action_key=released_feature_adapters[feature.key][2],
            )
            if feature.key in released_feature_adapters
            else feature
        )
        for feature in features
    ]
    features_by_key = {feature.key: feature for feature in features}
    resources = [
        ResourceProgression(
            resource.key,
            "resources" if resource.key == "fighter.second_wind" else "catalogue",
            resource.maxima,
            resource.recovery_profile,
            resource.display_name,
            resource.srd_reference,
            resource.spend_profile,
            resource.capacity_expression,
            "released" if resource.key == "fighter.second_wind" else "catalogued",
        )
        for resource in SRD_CLASS_RESOURCES.values()
    ]
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
                srd_reference=class_references[name],
            )
        )
        spell_keys: tuple[str, ...] = ()
        if name in spell_access_data:
            access_data = spell_access_data[name]
            spell_key = f"{key}.spell_access"
            spell_access.append(
                SpellAccess(
                    spell_key,
                    str(
                        summary["primary_ability"]
                        if isinstance(summary["primary_ability"], str)
                        else summary["primary_ability"][-1]
                    ),
                    access_data["cantrips"],
                    (0,) * MAX_CLASS_LEVEL,
                    access_data["prepared"],
                    access_data.get("spellbook_entries", (0,) * MAX_CLASS_LEVEL),
                    access_data["maximum"],
                    access_data["slots"],
                    access_data.get("pact_slots", _NO_PACT_SLOTS),
                    access_data.get("pact_levels", _NO_PACT_SLOTS),
                    access_data["reference"],
                    preparation_timing=access_data.get(
                        "preparation_timing", "long_rest"
                    ),
                )
            )
            spell_keys = (spell_key,)
        levels = tuple(
            LevelGrants(
                level,
                tuple(
                    feature_key
                    for feature_key in catalogued_feature_keys[name][level - 1]
                    if features_by_key[feature_key].release_state == "released"
                ),
                (),
                spell_keys,
                (skill_choice_key,) if level == 1 else (),
                tuple(
                    feature_key
                    for feature_key in catalogued_feature_keys[name][level - 1]
                    if features_by_key[feature_key].release_state == "catalogued"
                ),
                (catalogued_subclasses[name][0],) if level == 3 else (),
                catalogued_subclasses[name][1][level - 1],
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
                    if isinstance(primary, str) and name in spell_access_data
                    else (primary[-1] if name in spell_access_data else None)
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
                class_references[name],
                levels,
            )
        )
    return build_registry(definitions, features, resources, spell_access, choices)


CLASS_PROGRESSION = _default_registry()
CLASSES = CLASS_PROGRESSION.chargen_summaries()

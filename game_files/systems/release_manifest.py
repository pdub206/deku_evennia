"""Validated, immutable content census for the Milestone 3 alpha.

Progression and magic remain the owners of their detailed definitions.  This
module binds their exact released projections together so chargen, release
checks, and future diagnostics cannot infer availability from roadmap prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from systems.effects import EFFECT_REGISTRY
from systems.magic import MAGIC_REGISTRY, STANDARD_HANDLERS, MagicKind
from systems.progression import (CLASS_PROGRESSION, MAX_CLASS_LEVEL,
                                 SELECTABLE_CLASS_NAMES)

ALPHA_MANIFEST_VERSION = 1


class ReleaseManifestError(ValueError):
    """Raised when released progression and executable content disagree."""


@dataclass(frozen=True)
class ReleasedLevel:
    """One cumulative, executable row in the four-class alpha matrix."""

    level: int
    feature_keys: tuple[str, ...]
    resource_keys: tuple[str, ...]
    choice_keys: tuple[str, ...]
    spell_access_keys: tuple[str, ...]
    magic_action_keys: tuple[str, ...]
    tactical_action_keys: tuple[str, ...]


@dataclass(frozen=True)
class ReleasedClass:
    """One class's complete selectable level-1 through level-3 census."""

    key: str
    subclass: str
    level_cap: int
    feature_keys: tuple[str, ...]
    resource_keys: tuple[str, ...]
    choice_keys: tuple[str, ...]
    spell_access_keys: tuple[str, ...]
    magic_action_keys: tuple[str, ...]
    tactical_action_keys: tuple[str, ...]
    effect_keys: tuple[str, ...]
    handler_keys: tuple[str, ...]
    adaptation_action_keys: tuple[str, ...]
    help_keys: tuple[str, ...]
    srd_references: tuple[str, ...]
    levels: tuple[ReleasedLevel, ...]


@dataclass(frozen=True)
class AlphaReleaseManifest:
    """Versioned cross-registry identity and immutable class projections."""

    version: int
    progression_version: int
    progression_fingerprint: str
    magic_version: int
    magic_fingerprint: str
    classes: Mapping[str, ReleasedClass]


_SUBCLASSES = MappingProxyType(
    {
        "Cleric": "Life Domain",
        "Fighter": "Champion",
        "Rogue": "Thief",
        "Wizard": "Evoker",
    }
)

_CLASS_TACTICAL_ACTIONS = MappingProxyType(
    {
        "Cleric": (),
        "Fighter": ("aim", "bash", "kick"),
        "Rogue": ("aim", "backstab", "hide", "steady_aim"),
        "Wizard": (),
    }
)


def build_alpha_manifest(
    progression: Any = CLASS_PROGRESSION,
    magic: Any = MAGIC_REGISTRY,
) -> AlphaReleaseManifest:
    """Build and validate the exact selectable alpha content projection."""
    if tuple(progression.definitions) != SELECTABLE_CLASS_NAMES:
        raise ReleaseManifestError("The alpha class census is incomplete.")

    released: dict[str, ReleasedClass] = {}
    for class_key in SELECTABLE_CLASS_NAMES:
        definition = progression.class_for(class_key)
        _validate_standard_array(definition)
        levels = tuple(definition.levels)
        if tuple(level.level for level in levels) != tuple(
            range(1, MAX_CLASS_LEVEL + 1)
        ):
            raise ReleaseManifestError(f"{class_key} does not cover the alpha levels.")

        feature_keys = _unique(
            key for level in levels for key in level.automatic_feature_keys
        )
        resource_keys = _unique(key for level in levels for key in level.resource_keys)
        choice_keys = _unique(key for level in levels for key in level.choice_keys)
        spell_access_keys = _unique(
            key for level in levels for key in level.spell_access_keys
        )
        action_keys = tuple(
            action.key
            for action in magic.definitions.values()
            if any(access.class_key == class_key for access in action.class_access)
        )
        if any(key not in progression.features for key in feature_keys):
            raise ReleaseManifestError(f"{class_key} references an unknown feature.")
        if any(key not in progression.resources for key in resource_keys):
            raise ReleaseManifestError(f"{class_key} references an unknown resource.")
        if any(key not in progression.choices for key in choice_keys):
            raise ReleaseManifestError(f"{class_key} references an unknown choice.")
        if any(key not in progression.spell_access for key in spell_access_keys):
            raise ReleaseManifestError(f"{class_key} references unknown spell access.")
        _validate_actions(class_key, action_keys, progression, magic)
        tactical_action_keys = _CLASS_TACTICAL_ACTIONS[class_key]
        _validate_tactical_actions(class_key, tactical_action_keys)
        actions = tuple(magic.definitions[key] for key in action_keys)
        effect_keys = _unique(key for action in actions for key in action.effect_keys)
        handler_keys = _unique(action.handler_key for action in actions)
        adaptation_action_keys = tuple(
            action.key
            for action in actions
            if any(tag.startswith("alpha_") for tag in action.tags)
        )
        help_keys = _unique(
            (
                "class progression",
                *(progression.features[key].help_key for key in feature_keys),
                *(action.player_help.key for action in actions),
            )
        )
        _validate_help_keys(class_key, help_keys)
        srd_references = _unique(
            (
                definition.srd_reference,
                *(progression.features[key].srd_reference for key in feature_keys),
                *(progression.resources[key].srd_reference for key in resource_keys),
                *(progression.choices[key].srd_reference for key in choice_keys),
                *(
                    progression.spell_access[key].srd_reference
                    for key in spell_access_keys
                ),
                *(action.srd_reference for action in actions),
            )
        )
        released_levels = _released_level_matrix(
            definition, actions, tactical_action_keys, progression
        )
        released[class_key] = ReleasedClass(
            class_key,
            _SUBCLASSES[class_key],
            MAX_CLASS_LEVEL,
            feature_keys,
            resource_keys,
            choice_keys,
            spell_access_keys,
            action_keys,
            tactical_action_keys,
            effect_keys,
            handler_keys,
            adaptation_action_keys,
            help_keys,
            srd_references,
            released_levels,
        )

    return AlphaReleaseManifest(
        ALPHA_MANIFEST_VERSION,
        progression.version,
        progression.fingerprint,
        magic.version,
        magic.fingerprint,
        MappingProxyType(released),
    )


def _validate_actions(
    class_key: str, action_keys: tuple[str, ...], progression: Any, magic: Any
) -> None:
    """Require executable references and enough legal caster selections."""
    actions = tuple(magic.definitions[key] for key in action_keys)
    for action in actions:
        if action.handler_key not in STANDARD_HANDLERS:
            raise ReleaseManifestError(f"{action.key} has no released handler.")
        if any(EFFECT_REGISTRY.get(key) is None for key in action.effect_keys):
            raise ReleaseManifestError(f"{action.key} has an unknown effect.")
        if any(tag.startswith("alpha_") for tag in action.tags) and not (
            action.player_help.restrictions
        ):
            raise ReleaseManifestError(f"{action.key} has an undocumented adaptation.")
        if not action.srd_reference.startswith("SRD 5.2.1 "):
            raise ReleaseManifestError(f"{action.key} has no released SRD reference.")

    spell_access = progression.spell_access.get(f"{class_key.casefold()}.spell_access")
    if spell_access is None:
        if any(action.kind == MagicKind.SPELL for action in actions):
            raise ReleaseManifestError(f"{class_key} has spells without spell access.")
        return
    cantrips = sum(action.spell_level == 0 for action in actions)
    leveled = sum(action.spell_level > 0 for action in actions)
    if cantrips < spell_access.cantrips[MAX_CLASS_LEVEL - 1]:
        raise ReleaseManifestError(f"{class_key} lacks enough released cantrips.")
    required_spells = max(
        spell_access.spells_prepared[MAX_CLASS_LEVEL - 1],
        spell_access.spellbook_entries[MAX_CLASS_LEVEL - 1],
    )
    if leveled < required_spells:
        raise ReleaseManifestError(f"{class_key} lacks enough released spells.")
    if not any(
        action.spell_level == spell_access.maximum_spell_level[2] for action in actions
    ):
        raise ReleaseManifestError(f"{class_key} lacks its maximum spell level.")


def _validate_tactical_actions(class_key: str, action_keys: tuple[str, ...]) -> None:
    """Require every curated physical class-loop action to have a live handler."""
    from systems.tactical_combat import TACTICAL_ACTIONS

    if any(TACTICAL_ACTIONS.get(key) is None for key in action_keys):
        raise ReleaseManifestError(f"{class_key} has an unavailable tactical action.")


def _validate_standard_array(definition: Any) -> None:
    """Require chargen's released recommendation to support the class's core loop."""
    from world.chargen_data import (ABILITY_NAMES, STANDARD_ARRAY,
                                    STANDARD_ARRAY_BY_CLASS)

    assignment = STANDARD_ARRAY_BY_CLASS.get(definition.key)
    if (
        not isinstance(assignment, Mapping)
        or set(assignment) != set(ABILITY_NAMES)
        or sorted(assignment.values(), reverse=True)
        != sorted(STANDARD_ARRAY, reverse=True)
        or max(assignment[ability] for ability in definition.primary_abilities)
        != max(STANDARD_ARRAY)
        or (
            definition.spellcasting_ability is not None
            and assignment[definition.spellcasting_ability] != max(STANDARD_ARRAY)
        )
    ):
        raise ReleaseManifestError(
            f"{definition.key} has no viable standard-array recommendation."
        )


def _validate_help_keys(class_key: str, help_keys: tuple[str, ...]) -> None:
    """Cross-check all class and action help against the published file database."""
    from world.help_entries import HELP_ENTRY_DICTS

    published = {
        str(entry.get("key", "")).strip().casefold() for entry in HELP_ENTRY_DICTS
    }
    if any(key.casefold() not in published for key in help_keys):
        raise ReleaseManifestError(f"{class_key} has unpublished player help.")


def _released_level_matrix(
    definition: Any,
    actions: tuple[Any, ...],
    tactical_action_keys: tuple[str, ...],
    progression: Any,
) -> tuple[ReleasedLevel, ...]:
    """Expand cumulative grants and prove every alpha level has a usable action."""
    features: list[str] = []
    resources: list[str] = []
    choices: list[str] = []
    spell_access: list[str] = []
    rows: list[ReleasedLevel] = []
    for grants in definition.levels:
        features.extend(grants.automatic_feature_keys)
        resources.extend(grants.resource_keys)
        choices.extend(grants.choice_keys)
        spell_access.extend(grants.spell_access_keys)
        for choice_key in grants.choice_keys:
            choice = progression.choices[choice_key]
            if len(choice.legal_options) < choice.count:
                raise ReleaseManifestError(
                    f"{definition.key} level {grants.level} has an impossible choice."
                )
        available_magic = tuple(
            action.key
            for action in actions
            if any(
                access.class_key == definition.key
                and access.minimum_level <= grants.level
                for access in action.class_access
            )
        )
        if not available_magic and not tactical_action_keys:
            raise ReleaseManifestError(
                f"{definition.key} level {grants.level} has no released action."
            )
        rows.append(
            ReleasedLevel(
                grants.level,
                _unique(features),
                _unique(resources),
                _unique(choices),
                _unique(spell_access),
                available_magic,
                tactical_action_keys,
            )
        )
    return tuple(rows)


def _unique(values: Any) -> tuple[str, ...]:
    """Preserve declaration order while removing cumulative duplicate grants."""
    return tuple(dict.fromkeys(values))


ALPHA_RELEASE_MANIFEST = build_alpha_manifest()

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
from systems.progression import (
    CLASS_PROGRESSION,
    MAX_CLASS_LEVEL,
    SELECTABLE_CLASS_NAMES,
)

ALPHA_MANIFEST_VERSION = 1


class ReleaseManifestError(ValueError):
    """Raised when released progression and executable content disagree."""


@dataclass(frozen=True)
class ReleasedClass:
    """One class's complete selectable level-1 through level-3 census."""

    key: str
    subclass: str
    level_cap: int
    feature_keys: tuple[str, ...]
    resource_keys: tuple[str, ...]
    choice_keys: tuple[str, ...]
    magic_action_keys: tuple[str, ...]


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
        _validate_actions(class_key, action_keys, progression, magic)
        released[class_key] = ReleasedClass(
            class_key,
            _SUBCLASSES[class_key],
            MAX_CLASS_LEVEL,
            feature_keys,
            resource_keys,
            choice_keys,
            action_keys,
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


def _unique(values: Any) -> tuple[str, ...]:
    """Preserve declaration order while removing cumulative duplicate grants."""
    return tuple(dict.fromkeys(values))


ALPHA_RELEASE_MANIFEST = build_alpha_manifest()

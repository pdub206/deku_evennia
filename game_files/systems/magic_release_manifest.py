"""Fail-closed MAGIC-03 release-manifest validation.

P-04 releases individual SRD entries.  This module is deliberately separate:
it may publish a class only after every level from 1 through the normal level
cap has no catalogued class feature and every released action reference still
resolves.  An empty manifest is meaningful: no class kit is being represented
as complete merely because its source table is catalogued.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

from systems.magic import MAGIC_REGISTRY, MagicRegistry
from systems.progression import (
    CLASS_PROGRESSION,
    MAX_CLASS_LEVEL,
    ProgressionRegistry,
    RegistryValidationError,
)

MAGIC_RELEASE_MANIFEST_VERSION = 1
NORMAL_LEVEL_CAP = 20


class ReleaseManifestError(ValueError):
    """Raised when a proposed public class-kit release is incomplete."""


@dataclass(frozen=True)
class ClassLevelCoverage:
    """One class level's cumulative, source-backed release status."""

    class_key: str
    level: int
    released_feature_keys: tuple[str, ...]
    catalogued_feature_keys: tuple[str, ...]
    available_action_keys: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def playable(self) -> bool:
        """Return whether this level is complete enough to publish."""
        return not self.blockers


@dataclass(frozen=True)
class MagicReleaseManifest:
    """A versioned declaration of class kits available to players.

    Every declared class must contain exactly levels 1--20.  Keeping the
    declared set empty until that proof exists avoids a temporary lower-level
    cap or a prose-only claim of support.
    """

    version: int
    level_cap: int
    published_class_levels: Mapping[str, tuple[int, ...]]
    srd_reference: str
    fingerprint: str


def build_release_manifest(
    published_class_levels: Mapping[str, tuple[int, ...]],
    *,
    progression: ProgressionRegistry = CLASS_PROGRESSION,
    magic: MagicRegistry = MAGIC_REGISTRY,
    version: int = MAGIC_RELEASE_MANIFEST_VERSION,
    level_cap: int = NORMAL_LEVEL_CAP,
    srd_reference: str = "SRD 5.2.1 class feature tables and spellcasting tables",
) -> MagicReleaseManifest:
    """Validate and freeze a public class-kit declaration before publication."""
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != MAGIC_RELEASE_MANIFEST_VERSION
    ):
        raise ReleaseManifestError("Release manifest has an unsupported version.")
    if (
        isinstance(level_cap, bool)
        or not isinstance(level_cap, int)
        or level_cap != NORMAL_LEVEL_CAP
        or level_cap != MAX_CLASS_LEVEL
    ):
        raise ReleaseManifestError("The published level cap must be exactly 20.")
    if (
        not isinstance(srd_reference, str)
        or not srd_reference.startswith("SRD 5.2.1 ")
        or len(srd_reference) > 160
    ):
        raise ReleaseManifestError("Release manifest needs an SRD 5.2.1 reference.")
    if not isinstance(published_class_levels, Mapping):
        raise ReleaseManifestError("Published class levels must be a mapping.")

    normalized: dict[str, tuple[int, ...]] = {}
    required_levels = tuple(range(1, level_cap + 1))
    for class_key, levels in published_class_levels.items():
        if not isinstance(class_key, str):
            raise ReleaseManifestError("Published class key is invalid.")
        try:
            progression.class_for(class_key)
        except RegistryValidationError as err:
            raise ReleaseManifestError("Published class key is invalid.") from err
        if not isinstance(levels, tuple) or levels != required_levels:
            raise ReleaseManifestError(
                f"Published class '{class_key}' must cover every level from 1 through 20."
            )
        for level in levels:
            coverage = class_level_coverage(
                class_key, level, progression=progression, magic=magic
            )
            if not coverage.playable:
                raise ReleaseManifestError(
                    f"Published class '{class_key}' level {level} is incomplete: "
                    + ", ".join(coverage.blockers)
                )
        normalized[class_key] = levels

    payload = {
        "version": version,
        "level_cap": level_cap,
        "published_class_levels": normalized,
        "srd_reference": srd_reference,
        "progression_fingerprint": progression.fingerprint,
        "magic_fingerprint": magic.fingerprint,
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return MagicReleaseManifest(
        version,
        level_cap,
        MappingProxyType(normalized),
        srd_reference,
        fingerprint,
    )


def class_level_coverage(
    class_key: str,
    level: int,
    *,
    progression: ProgressionRegistry = CLASS_PROGRESSION,
    magic: MagicRegistry = MAGIC_REGISTRY,
) -> ClassLevelCoverage:
    """Expand cumulative grants and report every observable publication blocker."""
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 20:
        raise ReleaseManifestError("Class coverage level must be between 1 and 20.")
    try:
        definition = progression.class_for(class_key)
    except RegistryValidationError as err:
        raise ReleaseManifestError("Class coverage key is invalid.") from err

    released = tuple(
        dict.fromkeys(
            feature_key
            for grants in definition.levels[:level]
            for feature_key in grants.automatic_feature_keys
        )
    )
    catalogued = tuple(
        dict.fromkeys(
            feature_key
            for grants in definition.levels[:level]
            for feature_key in grants.catalogued_feature_keys
        )
    )
    catalogued_subclass = tuple(
        dict.fromkeys(
            feature_key
            for grants in definition.levels[:level]
            for feature_key in (
                *grants.catalogued_subclass_choice_keys,
                *grants.catalogued_subclass_feature_keys,
            )
        )
    )
    actions = magic.available_for(class_key, level)
    blockers = [f"catalogued_feature:{feature_key}" for feature_key in catalogued]
    blockers.extend(
        f"catalogued_subclass_feature:{feature_key}"
        for feature_key in catalogued_subclass
    )
    for feature_key in released:
        feature = progression.features[feature_key]
        if feature.action_key and not magic.is_available(feature.action_key):
            blockers.append(f"missing_action:{feature.action_key}")
    for choice_key in (
        key for grants in definition.levels[:level] for key in grants.choice_keys
    ):
        choice = progression.choices[choice_key]
        if (
            choice.release_state != "released"
            or len(choice.legal_options) < choice.count
        ):
            blockers.append(f"incomplete_choice:{choice_key}")
    has_spell_capacity = any(
        access.cantrips[level - 1]
        or any(slot_column[level - 1] for slot_column in access.spell_slots)
        or access.pact_slots[level - 1]
        for grants in definition.levels[:level]
        for access_key in grants.spell_access_keys
        for access in (progression.spell_access[access_key],)
    )
    if has_spell_capacity and not any(action.kind == "spell" for action in actions):
        blockers.append("missing_spell_action")

    return ClassLevelCoverage(
        class_key,
        level,
        released,
        catalogued,
        tuple(action.key for action in actions),
        tuple(blockers),
    )


# No class is publicly declared until P-04 has released and tested all of its
# cumulative level-1--20 source requirements.  The validator makes changing
# this declaration a checked, all-or-nothing operation.
MAGIC_03_RELEASE_MANIFEST = build_release_manifest({})

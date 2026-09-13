"""Read-only ADV-05 projections over advancement and progression owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from systems.advancement import (
    RELEASE_LEVEL_CAP,
    XP_THRESHOLDS,
    AdvancementError,
    AdvancementSnapshotError,
    AdvancementStateSnapshot,
    advancement_state_snapshot,
)
from systems.progression import CLASS_PROGRESSION
from systems.training import PracticeView, TrainingError, practice_view


@dataclass(frozen=True)
class ReleasedLevelView:
    """One released level's threshold and newly granted progression data."""

    level: int
    xp_threshold: int
    automatic_features: tuple[str, ...]
    prerequisites: tuple[tuple[str, tuple[str, ...]], ...]
    choices: tuple[str, ...]
    resources: tuple[tuple[str, int], ...]
    spell_access: tuple[tuple[str, int, int, int, int, tuple[int, ...]], ...]


@dataclass(frozen=True)
class AdvancementInfo:
    """Safe immutable data shared by score, levels, and staff inspection."""

    valid: bool
    reason: str
    state: AdvancementStateSnapshot | None
    practice: PracticeView | None
    levels: tuple[ReleasedLevelView, ...]
    next_threshold: int | None
    xp_remaining: int | None
    capped: bool
    diagnostic: str | None


def advancement_info(character: Any) -> AdvancementInfo:
    """Return a complete view or one bounded repair-safe failure."""
    try:
        state = advancement_state_snapshot(character)
        practice = practice_view(character)
        definition = CLASS_PROGRESSION.class_for(state.class_key)
    except AdvancementSnapshotError as err:
        return AdvancementInfo(
            False,
            "Advancement information needs staff review.",
            None,
            None,
            (),
            None,
            None,
            False,
            err.diagnostic,
        )
    except (AdvancementError, TrainingError, ValueError, TypeError, KeyError):
        return AdvancementInfo(
            False,
            "Advancement information needs staff review.",
            None,
            None,
            (),
            None,
            None,
            False,
            "invalid_entitlement_or_advancement_state",
        )

    levels = tuple(
        _level_view(definition.grants_at(level), level)
        for level in range(1, RELEASE_LEVEL_CAP + 1)
    )
    capped = state.level == RELEASE_LEVEL_CAP
    next_threshold = None if capped else XP_THRESHOLDS[state.level]
    return AdvancementInfo(
        True,
        "ok",
        state,
        practice,
        levels,
        next_threshold,
        None if capped else max(0, next_threshold - state.xp),
        capped,
        None,
    )


def display_name(key: str) -> str:
    """Render a stable registry key without exposing its internal namespace."""
    return key.rsplit(".", 1)[-1].replace("_", " ").title()


def _level_view(grants: Any, level: int) -> ReleasedLevelView:
    """Project one ADV-02 level without copying its definitions."""
    resources = tuple(
        (display_name(key), CLASS_PROGRESSION.resources[key].maxima[level - 1])
        for key in grants.resource_keys
    )
    spell_access = tuple(
        (
            display_name(key),
            CLASS_PROGRESSION.spell_access[key].cantrips[level - 1],
            CLASS_PROGRESSION.spell_access[key].spells_known[level - 1],
            CLASS_PROGRESSION.spell_access[key].spells_prepared[level - 1],
            CLASS_PROGRESSION.spell_access[key].spellbook_entries[level - 1],
            tuple(
                slots[level - 1]
                for slots in CLASS_PROGRESSION.spell_access[key].spell_slots
            ),
        )
        for key in grants.spell_access_keys
    )
    return ReleasedLevelView(
        level,
        XP_THRESHOLDS[level - 1],
        tuple(display_name(key) for key in grants.automatic_feature_keys),
        tuple(
            (
                display_name(key),
                tuple(
                    display_name(required)
                    for required in CLASS_PROGRESSION.features[key].prerequisites
                ),
            )
            for key in grants.automatic_feature_keys
            if CLASS_PROGRESSION.features[key].prerequisites
        ),
        tuple(display_name(key) for key in grants.choice_keys),
        resources,
        spell_access,
    )

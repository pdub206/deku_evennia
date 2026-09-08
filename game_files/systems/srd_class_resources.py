"""SRD 5.2.1 non-spell class-resource catalogue.

These records describe source capacity, spending, and recovery without
publishing an action.  A record remains catalogue-only until the feature that
uses it has a complete owning adapter, help, and focused tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from systems.progression import MAX_CLASS_LEVEL


@dataclass(frozen=True)
class SRDClassResource:
    """One cited non-spell resource and its exact source-side policy.

    ``maxima`` holds the complete 1--20 table when capacity is independent of
    an ability score.  For an ability-derived capacity it is empty and
    ``capacity_expression`` is the complete rule the eventual adapter must
    calculate.  Neither form is player-facing by itself.
    """

    key: str
    display_name: str
    maxima: tuple[int, ...]
    capacity_expression: str
    spend_profile: str
    recovery_profile: str
    srd_reference: str


def _table(
    key: str,
    display_name: str,
    maxima: tuple[int, ...],
    spend_profile: str,
    recovery_profile: str,
    reference: str,
) -> SRDClassResource:
    """Build and guard one complete fixed-capacity source record."""
    if len(maxima) != MAX_CLASS_LEVEL:
        raise ValueError(f"{key} needs {MAX_CLASS_LEVEL} capacity entries.")
    return SRDClassResource(
        key,
        display_name,
        maxima,
        "",
        spend_profile,
        recovery_profile,
        reference,
    )


def _formula(
    key: str,
    display_name: str,
    capacity_expression: str,
    spend_profile: str,
    recovery_profile: str,
    reference: str,
) -> SRDClassResource:
    """Build one ability-derived resource without inventing a numeric curve."""
    return SRDClassResource(
        key,
        display_name,
        (),
        capacity_expression,
        spend_profile,
        recovery_profile,
        reference,
    )


SRD_CLASS_RESOURCES: Mapping[str, SRDClassResource] = MappingProxyType(
    {
        "barbarian.rage": _table(
            "barbarian.rage",
            "Rage",
            (2, 2, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6),
            "one_use_per_rage",
            "short_rest_one_long_rest_full",
            "SRD 5.2.1 p.28: Rage; Barbarian Features table",
        ),
        "bard.bardic_inspiration": _formula(
            "bard.bardic_inspiration",
            "Bardic Inspiration",
            "max(1, Charisma modifier)",
            "one_die_per_inspiration",
            "long_rest_full_until_level_4; short_or_long_rest_full_from_level_5",
            "SRD 5.2.1 pp.31-32: Bardic Inspiration; Font of Inspiration",
        ),
        "cleric.channel_divinity": _table(
            "cleric.channel_divinity",
            "Channel Divinity",
            (0, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4),
            "one_use_per_channel_divinity_effect",
            "short_rest_one_long_rest_full",
            "SRD 5.2.1 p.36: Cleric Features table; p.45: Channel Divinity",
        ),
        "druid.wild_shape": _table(
            "druid.wild_shape",
            "Wild Shape",
            (0, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4),
            "one_use_per_transformation",
            "short_rest_one_long_rest_full",
            "SRD 5.2.1 p.41: Druid Features table; p.44: Wild Shape",
        ),
        "fighter.second_wind": _table(
            "fighter.second_wind",
            "Second Wind",
            (2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
            "one_use_per_second_wind_or_tactical_mind",
            "short_rest_one_long_rest_full",
            "SRD 5.2.1 pp.47-48: Fighter Features table; Second Wind",
        ),
        "monk.focus": _table(
            "monk.focus",
            "Focus Points",
            (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
            "feature_declared_focus_cost",
            "short_or_long_rest_full",
            "SRD 5.2.1 pp.50-51: Monk Features table; Monk's Focus",
        ),
        "paladin.lay_on_hands": _table(
            "paladin.lay_on_hands",
            "Lay On Hands",
            tuple(5 * level for level in range(1, MAX_CLASS_LEVEL + 1)),
            "one_or_more_points_per_healing_or_cure",
            "long_rest_full",
            "SRD 5.2.1 p.53: Lay On Hands",
        ),
        "ranger.favored_enemy": _table(
            "ranger.favored_enemy",
            "Favored Enemy",
            (2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6),
            "one_free_hunters_mark_cast",
            "long_rest_full",
            "SRD 5.2.1 pp.56-57: Favored Enemy; Ranger Features table",
        ),
        "rogue.cunning_strike_dice": _table(
            "rogue.cunning_strike_dice",
            "Cunning Strike Dice",
            (0, 0, 0, 0, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10),
            "forfeit_sneak_attack_dice_on_hit",
            "per_eligible_attack",
            "SRD 5.2.1 pp.61-62: Rogue Features table; Cunning Strike",
        ),
        "sorcerer.sorcery_points": _table(
            "sorcerer.sorcery_points",
            "Sorcery Points",
            (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
            "feature_declared_sorcery_point_cost",
            "long_rest_full",
            "SRD 5.2.1 pp.64-65: Font of Magic; Sorcerer Features table",
        ),
        "wizard.arcane_recovery": _table(
            "wizard.arcane_recovery",
            "Arcane Recovery",
            (1,) * MAX_CLASS_LEVEL,
            "one_short_rest_use_to_recover_slots_under_level_6_totaling_half_wizard_level_rounded_up",
            "long_rest_full",
            "SRD 5.2.1 pp.77-78: Arcane Recovery; Wizard Features table",
        ),
    }
)

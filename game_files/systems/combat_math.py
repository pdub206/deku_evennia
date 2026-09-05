"""Pure, shared calculations for live attacks, estimates, and balance runs.

Nothing in this module knows about Evennia objects, Attributes, messages, or
process-global randomness.  Callers provide an already-derived attack profile,
mitigation function, hit-location picker, and die roller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from systems.character_stats import AttackProfile
from systems.dice import _DAMAGE_EXPRESSION, roll_damage_expression

HIT_LOCATION_WEIGHTS = {
    "head": 6,
    "neck": 2,
    "body": 30,
    "right shoulder": 5,
    "left shoulder": 5,
    "right arm": 8,
    "left arm": 8,
    "right wrist": 3,
    "left wrist": 3,
    "right hand": 3,
    "left hand": 3,
    "right leg": 9,
    "left leg": 9,
    "right foot": 3,
    "left foot": 3,
}


class AttackClassification(str, Enum):
    """The mechanical result of one d20 attack roll."""

    MISS = "miss"
    HIT = "hit"
    CRITICAL = "critical"


@dataclass(frozen=True)
class CalculatedAttack:
    """Side-effect-free result of rolling and mitigating one accepted attack."""

    attack_rolls: tuple[int, ...]
    die_roll: int
    total: int
    armor_class: int
    outcome: AttackClassification
    hit_location: str | None = None
    damage_rolls: tuple[int, ...] = ()
    damage_total: int = 0
    mitigation: Any | None = None
    final_damage: int = 0


def resolve_attack_calculation(
    profile: AttackProfile,
    armor_class: int,
    *,
    target_unconscious: bool,
    attacker_untrained_armor: bool,
    has_advantage: bool,
    has_disadvantage: bool,
    extra_damage_dice: str | None,
    roller: Callable[[int], int],
    select_location: Callable[[], str],
    mitigate: Callable[[int, str, str], Any],
) -> CalculatedAttack:
    """Resolve rolls, classification, location, dice, and mitigation only."""
    has_advantage = has_advantage or target_unconscious
    has_disadvantage = has_disadvantage or attacker_untrained_armor
    attack_rolls = (
        (roller(20), roller(20)) if has_advantage != has_disadvantage else (roller(20),)
    )
    _validate_rolls(attack_rolls, 20, "Attack")
    die_roll = (
        max(attack_rolls)
        if has_advantage and not has_disadvantage
        else min(attack_rolls)
    )
    total = die_roll + profile.attack_bonus
    if die_roll == 1:
        outcome = AttackClassification.MISS
    elif die_roll == 20 or target_unconscious:
        outcome = AttackClassification.CRITICAL
    elif total >= armor_class:
        outcome = AttackClassification.HIT
    else:
        outcome = AttackClassification.MISS
    if outcome is AttackClassification.MISS:
        return CalculatedAttack(attack_rolls, die_roll, total, armor_class, outcome)

    location = select_location()
    multiplier = 2 if outcome is AttackClassification.CRITICAL else 1
    damage_rolls: tuple[int, ...] = ()
    dice_total = 0
    for expression in (profile.damage_dice, extra_damage_dice):
        if expression:
            damage_roll = roll_damage_expression(
                expression, multiplier=multiplier, roller=roller
            )
            damage_rolls += damage_roll.rolls
            dice_total += damage_roll.total
    damage_total = max(0, dice_total + profile.damage_base + profile.damage_bonus)
    mitigation = mitigate(damage_total, location, profile.damage_type)
    return CalculatedAttack(
        attack_rolls,
        die_roll,
        total,
        armor_class,
        outcome,
        location,
        damage_rolls,
        damage_total,
        mitigation,
        int(mitigation.final),
    )


def hit_probability(
    attack_bonus: int, armor_class: int, *, unconscious: bool = False
) -> float:
    """Return the exact d20 chance an ordinary attack causes a hit."""
    return (
        sum(
            1
            for die in range(1, 21)
            if _classify_die(die, attack_bonus, armor_class, unconscious)
            is not AttackClassification.MISS
        )
        / 20
    )


def critical_probability(
    attack_bonus: int, armor_class: int, *, unconscious: bool = False
) -> float:
    """Return the exact d20 critical chance under the live resolver's rules."""
    return (
        sum(
            1
            for die in range(1, 21)
            if _classify_die(die, attack_bonus, armor_class, unconscious)
            is AttackClassification.CRITICAL
        )
        / 20
    )


def expected_damage_per_action(
    profile: AttackProfile,
    armor_class: int,
    *,
    mitigate: Callable[[int, str, str], Any],
    location_weights: Mapping[str, int],
    target_unconscious: bool = False,
) -> float:
    """Return exact d20 and weighted-location expected final damage per action."""
    if not location_weights or any(
        not isinstance(weight, int) or weight <= 0
        for weight in location_weights.values()
    ):
        raise ValueError("Hit-location weights must be positive integers.")
    normal_raw = max(0, int(_average_damage(profile, multiplier=1)))
    critical_raw = max(0, int(_average_damage(profile, multiplier=2)))
    normal_damage = _weighted_mitigation(
        normal_raw, profile.damage_type, mitigate, location_weights
    )
    critical_damage = _weighted_mitigation(
        critical_raw, profile.damage_type, mitigate, location_weights
    )
    expected = 0.0
    for die in range(1, 21):
        outcome = _classify_die(
            die, profile.attack_bonus, armor_class, target_unconscious
        )
        if outcome is AttackClassification.HIT:
            expected += normal_damage / 20
        elif outcome is AttackClassification.CRITICAL:
            expected += critical_damage / 20
    return expected


def average_damage_expression(expression: str | None, *, multiplier: int = 1) -> float:
    """Return an expression's dice mean, applying its modifier only once."""
    if not expression:
        return 0.0
    match = _DAMAGE_EXPRESSION.fullmatch(expression.strip().lower().replace(" ", ""))
    if match is None:
        raise ValueError("Damage dice must use NdM notation.")
    return int(match["count"]) * multiplier * (int(match["sides"]) + 1) / 2 + int(
        match["modifier"] or 0
    )


def _classify_die(
    die_roll: int, attack_bonus: int, armor_class: int, unconscious: bool
) -> AttackClassification:
    if die_roll == 1:
        return AttackClassification.MISS
    if die_roll == 20 or unconscious:
        return AttackClassification.CRITICAL
    return (
        AttackClassification.HIT
        if die_roll + attack_bonus >= armor_class
        else AttackClassification.MISS
    )


def _average_damage(profile: AttackProfile, *, multiplier: int) -> float:
    return (
        profile.damage_base
        + profile.damage_bonus
        + average_damage_expression(profile.damage_dice, multiplier=multiplier)
    )


def _weighted_mitigation(
    amount: int,
    damage_type: str,
    mitigate: Callable[[int, str, str], Any],
    location_weights: Mapping[str, int],
) -> float:
    total_weight = sum(location_weights.values())
    return (
        sum(
            int(mitigate(amount, location, damage_type).final) * weight
            for location, weight in location_weights.items()
        )
        / total_weight
    )


def _validate_rolls(values: tuple[int, ...], sides: int, label: str) -> None:
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= sides
        for value in values
    ):
        raise ValueError(f"{label} roller returned an invalid d{sides} result.")

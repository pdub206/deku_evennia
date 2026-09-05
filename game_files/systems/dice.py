"""
Dice rolling system — the single source of truth for all randomised rolls.

=============================================================================
USAGE RULE: Every roll made anywhere in this game MUST go through this module.
Do NOT call random.randint, random.randrange, or any other RNG directly in
game logic.  Import from here instead:

    from systems.dice import d20, advantage, disadvantage, roll_check

This ensures consistent behaviour, makes future changes (logging, cheating
prevention, test seeding) trivial, and keeps the rules in one place.
=============================================================================

Public API
----------
d20() -> int
    Roll one d20.  Returns 1–20.

advantage() -> int
    Roll 2d20, return the higher result (SRD rule: favourable circumstances).

disadvantage() -> int
    Roll 2d20, return the lower result (SRD rule: unfavourable circumstances).

roll_check(bonus, dc, *, has_advantage=False, has_disadvantage=False) -> RollResult
    Roll a d20, apply modifier, compare to DC.  Honours the SRD rule that
    advantage and disadvantage cancel each other out.

roll(sides) -> int
    Roll a single die with `sides` faces.  Use for hit dice, damage, etc.
    e.g. roll(6) for 1d6, roll(12) for 1d12.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Low-level primitives
# ---------------------------------------------------------------------------


def roll(sides: int) -> int:
    """Roll one die with the given number of sides. Returns 1–sides."""
    if sides < 1:
        raise ValueError(f"Die must have at least 1 side, got {sides}")
    return random.randint(1, sides)


MAX_DAMAGE_DICE = 20
MAX_DAMAGE_SIDES = 1000
MAX_DAMAGE_MODIFIER = 1000
_DAMAGE_EXPRESSION = re.compile(
    r"^(?P<count>[1-9]\d*)d(?P<sides>[1-9]\d*)(?P<modifier>[+-]\d+)?$"
)


@dataclass(frozen=True)
class DamageRoll:
    """One bounded damage-expression roll with every individual die retained."""

    expression: str
    rolls: tuple[int, ...]
    modifier: int
    total: int


def roll_damage_expression(
    expression: str,
    *,
    multiplier: int = 1,
    roller: Callable[[int], int] = roll,
) -> DamageRoll:
    """Roll a safe builder-approved ``NdM[+/-K]`` damage expression.

    ``multiplier`` repeats dice only, which lets a critical roll the weapon's
    dice twice while applying the expression modifier once.  Every random value
    remains delegated to this module's normal single-die primitive by default.
    """
    if not isinstance(expression, str):
        raise ValueError("Damage dice must be text.")
    match = _DAMAGE_EXPRESSION.fullmatch(expression.strip().lower().replace(" ", ""))
    if match is None:
        raise ValueError("Damage dice must use NdM notation.")
    count, sides = int(match["count"]), int(match["sides"])
    modifier = int(match["modifier"] or 0)
    if count > MAX_DAMAGE_DICE or sides > MAX_DAMAGE_SIDES:
        raise ValueError("Damage dice exceed the supported limit.")
    if abs(modifier) > MAX_DAMAGE_MODIFIER:
        raise ValueError("Damage modifier exceeds the supported limit.")
    if (
        isinstance(multiplier, bool)
        or not isinstance(multiplier, int)
        or multiplier < 1
    ):
        raise ValueError("Damage multiplier must be a positive integer.")
    if count * multiplier > MAX_DAMAGE_DICE * 2:
        raise ValueError("Critical damage dice exceed the supported limit.")

    rolls = tuple(roller(sides) for _ in range(count * multiplier))
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= sides
        for value in rolls
    ):
        raise ValueError("Damage roller returned an invalid die result.")
    return DamageRoll(
        expression.strip().lower().replace(" ", ""),
        rolls,
        modifier,
        sum(rolls) + modifier,
    )


def d20() -> int:
    """Roll one d20. Returns 1–20."""
    return roll(20)


def advantage() -> int:
    """Roll 2d20 and return the higher result."""
    a, b = roll(20), roll(20)
    return max(a, b)


def disadvantage() -> int:
    """Roll 2d20 and return the lower result."""
    a, b = roll(20), roll(20)
    return min(a, b)


# ---------------------------------------------------------------------------
# Structured check result
# ---------------------------------------------------------------------------


@dataclass
class RollResult:
    """The outcome of a roll_check call."""

    die_roll: int
    bonus: int
    total: int
    dc: int
    success: bool
    # True when advantage/disadvantage cancelled each other out.
    cancelled: bool = False

    def __str__(self) -> str:
        mode = " (cancelled adv/dis)" if self.cancelled else ""
        outcome = "success" if self.success else "failure"
        return (
            f"d20({self.die_roll}) {self.bonus:+d} = {self.total} "
            f"vs DC {self.dc} → {outcome}{mode}"
        )


def roll_check(
    bonus: int,
    dc: int,
    *,
    has_advantage: bool = False,
    has_disadvantage: bool = False,
) -> RollResult:
    """
    Roll a d20 check against a Difficulty Class.

    Per SRD: if a character has both advantage and disadvantage they cancel
    out and the roll is straight regardless of how many sources of each exist.

    Args:
        bonus: The total modifier added to the roll (ability mod + proficiency,
               etc.).
        dc: The Difficulty Class to meet or beat.
        has_advantage: Whether the roller has advantage.
        has_disadvantage: Whether the roller has disadvantage.

    Returns:
        A RollResult describing the die face, total, and whether it succeeded.
    """
    cancelled = has_advantage and has_disadvantage
    if cancelled or (not has_advantage and not has_disadvantage):
        die_roll = d20()
    elif has_advantage:
        die_roll = advantage()
    else:
        die_roll = disadvantage()

    total = die_roll + bonus
    return RollResult(
        die_roll=die_roll,
        bonus=bonus,
        total=total,
        dc=dc,
        success=total >= dc,
        cancelled=cancelled,
    )

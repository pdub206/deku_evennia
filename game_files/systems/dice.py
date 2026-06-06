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
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Low-level primitives
# ---------------------------------------------------------------------------


def roll(sides: int) -> int:
    """Roll one die with the given number of sides. Returns 1–sides."""
    if sides < 1:
        raise ValueError(f"Die must have at least 1 side, got {sides}")
    return random.randint(1, sides)


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

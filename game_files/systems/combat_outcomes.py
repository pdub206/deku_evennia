"""Pure injury and reward calculations shared by live and offline combat."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InjuryState(str, Enum):
    """Stable consciousness states imposed by the injury system."""

    CONSCIOUS = "conscious"
    DYING = "dying"
    INCAPACITATED = "incapacitated"
    DEAD = "dead"


@dataclass(frozen=True)
class PredictedDamageTransition:
    """Pure immediate injury result used by live and offline combat."""

    resulting_hp: int
    state: InjuryState
    successes: int
    failures: int
    reason: str


def predict_damage_transition(
    hp: int,
    hp_max: int,
    state: InjuryState,
    successes: int,
    failures: int,
    amount: int,
    *,
    critical: bool,
    uses_death_saves: bool,
) -> PredictedDamageTransition:
    """Calculate ``apply_damage``'s immediate HP/injury rule without mutation."""
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (hp, hp_max, successes, failures, amount)
        )
        or hp < 0
        or hp > hp_max
        or hp_max < 1
        or amount < 0
        or successes < 0
        or failures < 0
    ):
        raise ValueError("Damage transition inputs are invalid.")
    if state is InjuryState.DEAD:
        return PredictedDamageTransition(hp, state, successes, failures, "dead")
    if amount == 0:
        return PredictedDamageTransition(hp, state, successes, failures, "no_damage")
    resulting_hp = max(0, hp - amount)
    if hp > 0 and resulting_hp == 0:
        if amount - hp >= hp_max or not uses_death_saves:
            return PredictedDamageTransition(
                0,
                InjuryState.DEAD,
                0,
                0,
                "massive_damage" if amount - hp >= hp_max else "reduced_to_zero",
            )
        return PredictedDamageTransition(0, InjuryState.DYING, 0, 0, "reduced_to_zero")
    if resulting_hp == 0 and state in {InjuryState.DYING, InjuryState.INCAPACITATED}:
        next_failures = min(3, failures + (2 if critical else 1))
        reason = "death_save_failure" if state is InjuryState.DYING else "destabilized"
        if next_failures >= 3:
            return PredictedDamageTransition(
                0, InjuryState.DEAD, successes, next_failures, reason
            )
        return PredictedDamageTransition(
            0, InjuryState.DYING, successes, next_failures, reason
        )
    return PredictedDamageTransition(
        resulting_hp, state, successes, failures, "damaged"
    )


def calculate_npc_xp(
    base_xp: int, npc_level: int, recipient_level: int
) -> tuple[float, int]:
    """Calculate a COMBAT-07 NPC award without recipients or persistence."""
    if (
        isinstance(base_xp, bool)
        or not isinstance(base_xp, int)
        or not 0 <= base_xp <= 1_000_000
        or isinstance(npc_level, bool)
        or not isinstance(npc_level, int)
        or npc_level < 1
        or isinstance(recipient_level, bool)
        or not isinstance(recipient_level, int)
        or recipient_level < 1
    ):
        raise ValueError("XP calculation inputs are invalid.")
    multiplier = max(0.0, min(2.0, 1.0 + 0.1 * (npc_level - recipient_level)))
    final_xp = int(base_xp * multiplier)
    if base_xp > 0 and multiplier > 0:
        final_xp = max(1, final_xp)
    return multiplier, final_xp

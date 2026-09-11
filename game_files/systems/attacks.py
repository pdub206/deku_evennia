"""Basic-attack policy, resolution, and presentation for COMBAT-02.

This module owns every rule between choosing a legal target and changing HP.
Commands and the combat pulse only ask it for a decision or structured result;
they never reconstruct rolls, mitigation, or messages themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from systems.action_policy import ActionCategory
from systems.character_stats import AttackProfile
from systems.combat import CombatActionResult
from systems.combat_math import HIT_LOCATION_WEIGHTS, resolve_attack_calculation
from systems.dice import roll
from systems.equipment import HIT_LOCATIONS, DamageMitigation
from systems.injury import (
    InjuryError,
    InjuryState,
    announce_transition,
    apply_damage,
    injury_record,
)
from systems.pulses import PulseEvent


class AttackOutcome(str, Enum):
    """The externally useful classification of an attack roll."""

    REJECTED = "rejected"
    MISS = "miss"
    HIT = "hit"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AttackabilityDecision:
    """A side-effect-free authorization decision with a stable safe reason."""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class AttackResult(CombatActionResult):
    """Immutable evidence of a complete basic-attack pipeline."""

    accepted: bool = False
    reason: str = ""
    attacker_id: int | None = None
    target_id: int | None = None
    attack_name: str = ""
    attack_rolls: tuple[int, ...] = ()
    die_roll: int | None = None
    attack_bonus: int = 0
    total: int | None = None
    armor_class: int | None = None
    outcome: AttackOutcome = AttackOutcome.REJECTED
    hit_location: str | None = None
    damage_rolls: tuple[int, ...] = ()
    damage_total: int = 0
    damage_type: str = ""
    mitigation: DamageMitigation | None = None
    final_damage: int = 0
    resulting_hp: int | None = None
    remove_target: bool = False


HitLocationSelector = Callable[[Any, Any], str]

# Body and limbs are deliberately common; the complete initial target set is
# derived from equipment's canonical list rather than copied into combat code.
if set(HIT_LOCATION_WEIGHTS) != set(HIT_LOCATIONS) or any(
    weight <= 0 for weight in HIT_LOCATION_WEIGHTS.values()
):
    raise RuntimeError("Combat hit-location weights must cover exactly HIT_LOCATIONS.")


def select_hit_location(attacker: Any, target: Any) -> str:
    """Choose one weighted, supported hit location using the shared die roller."""
    total_weight = sum(HIT_LOCATION_WEIGHTS.values())
    selected = roll(total_weight)
    for location in HIT_LOCATIONS:
        selected -= HIT_LOCATION_WEIGHTS[location]
        if selected <= 0:
            return location
    raise RuntimeError("A valid hit-location roll did not select a location.")


def can_attack(attacker: Any, target: Any) -> AttackabilityDecision:
    """Authorize an attack consistently for commands, pulses, AI, and abilities."""
    if not _is_character(attacker) or not _is_character(target):
        return AttackabilityDecision(False, "not_character")
    if attacker.id == target.id:
        return AttackabilityDecision(False, "self")
    if attacker.location is None or attacker.location != target.location:
        return AttackabilityDecision(False, "not_colocated")
    from systems.mobile_policy import is_protected, may_enter_combat

    entry = may_enter_combat(attacker)
    if not entry.allowed:
        return AttackabilityDecision(False, entry.reason)
    if not attacker.actions.check(ActionCategory.COMBAT).allowed:
        return AttackabilityDecision(False, "attacker_ineligible")
    try:
        target_injury = injury_record(target)
    except InjuryError:
        return AttackabilityDecision(False, "target_ineligible")
    if target_injury.state is InjuryState.DEAD:
        return AttackabilityDecision(False, "target_defeated")
    if is_protected(target):
        return AttackabilityDecision(False, "protected")
    if _is_staff_immune(target):
        return AttackabilityDecision(False, "staff_immune")
    if not target.access(attacker, "attack", default=True):
        return AttackabilityDecision(False, "access_denied")
    return AttackabilityDecision(True)


def resolve_basic_attack(
    attacker: Any,
    target: Any,
    event: PulseEvent | None = None,
    *,
    die_roller: Callable[[int], int] = roll,
    location_selector: HitLocationSelector = select_hit_location,
    emit_messages: bool = True,
    profile: AttackProfile | None = None,
    has_advantage: bool = False,
    has_disadvantage: bool = False,
    extra_damage_dice: str | None = None,
    attack_name: str | None = None,
) -> AttackResult:
    """Resolve one revalidated attack through the shared damage pipeline.

    Tactical actions may supply a temporary profile, roll state, location, or
    extra dice.  They still use this one resolver for hit, critical, armor,
    injury, and ordinary combat messages.
    """
    decision = can_attack(attacker, target)
    attacker_id = getattr(attacker, "id", None)
    target_id = getattr(target, "id", None)
    if not decision.allowed:
        return AttackResult(
            reason=decision.reason, attacker_id=attacker_id, target_id=target_id
        )

    profile = profile or attacker.stats.attack_profile()
    guiding_bolt = target.effects.has_condition("guiding_bolt_marked")
    has_advantage = has_advantage or guiding_bolt
    has_disadvantage = has_disadvantage or target.effects.has_condition("blurred")
    target_injury = injury_record(target)
    target_unconscious = target_injury.state in {
        InjuryState.DYING,
        InjuryState.INCAPACITATED,
    }
    calculation = resolve_attack_calculation(
        profile,
        target.stats.armor_class,
        target_unconscious=target_unconscious,
        attacker_untrained_armor=attacker.stats.has_untrained_armor,
        has_advantage=has_advantage,
        has_disadvantage=has_disadvantage,
        extra_damage_dice=extra_damage_dice,
        roller=die_roller,
        select_location=lambda: location_selector(attacker, target),
        mitigate=target.stats.mitigate_damage,
    )
    if guiding_bolt:
        instance = next(
            (
                effect
                for effect in target.effects.all()
                if "guiding_bolt_marked" in effect.conditions
            ),
            None,
        )
        if instance is not None:
            target.effects.remove(instance.instance_id, quiet=True)
    if (
        calculation.hit_location is not None
        and calculation.hit_location not in HIT_LOCATIONS
    ):
        raise ValueError("Hit-location selector returned an unsupported location.")
    outcome = AttackOutcome(calculation.outcome.value)

    common = {
        "accepted": True,
        "attacker_id": attacker.id,
        "target_id": target.id,
        "attack_name": attack_name or profile.name,
        "attack_rolls": calculation.attack_rolls,
        "die_roll": calculation.die_roll,
        "attack_bonus": profile.attack_bonus,
        "total": calculation.total,
        "armor_class": calculation.armor_class,
        "outcome": outcome,
        "damage_type": profile.damage_type,
    }
    if outcome is AttackOutcome.MISS:
        result = AttackResult(acted=True, **common)
        if emit_messages:
            render_attack_result(attacker, target, result)
        return result

    injury = apply_damage(
        target,
        calculation.final_damage,
        critical=outcome is AttackOutcome.CRITICAL,
        emit_messages=False,
        source=attacker,
    )
    result = AttackResult(
        acted=True,
        hit_location=calculation.hit_location,
        damage_rolls=calculation.damage_rolls,
        damage_total=calculation.damage_total,
        mitigation=calculation.mitigation,
        final_damage=calculation.final_damage,
        resulting_hp=injury.resulting_hp,
        remove_target=injury.combat_cleanup_required,
        **common,
    )
    if emit_messages:
        render_attack_result(attacker, target, result)
        announce_transition(target, injury)
    return result


def render_attack_result(attacker: Any, target: Any, result: AttackResult) -> None:
    """Deliver one recipient-specific, player-safe combat message per character."""
    if (
        not result.accepted
        or attacker.location is None
        or attacker.location != target.location
    ):
        return
    for observer in attacker.location.contents_get(content_type="character"):
        if observer is attacker:
            observer.msg(_message_for_attacker(attacker, target, result))
        elif observer is target:
            observer.msg(_message_for_target(attacker, target, result))
        else:
            observer.msg(_message_for_observer(attacker, target, observer, result))


def _message_for_attacker(attacker: Any, target: Any, result: AttackResult) -> str:
    """Render the attacker-only view without hidden mechanical values."""
    name = target.get_display_name(attacker)
    if result.outcome is AttackOutcome.MISS:
        message = f"You miss {name}."
    elif result.final_damage == 0:
        message = f"Your blow against {name}'s {result.hit_location} is fully absorbed."
    else:
        verb = (
            "critically strike"
            if result.outcome is AttackOutcome.CRITICAL
            else "strike"
        )
        message = f"You {verb} {name}'s {result.hit_location} for {result.final_damage} damage."
    # Detailed mechanics are intentionally limited to the character who made
    # this roll; target AC and every other character's private stats stay hidden.
    try:
        from systems.combat_controls import get_combat_verbose

        if get_combat_verbose(attacker) == "detailed":
            damage = ", ".join(str(value) for value in result.damage_rolls) or "-"
            mitigation = result.mitigation.prevented if result.mitigation else 0
            message += (
                f" [d20 {result.die_roll} + {result.attack_bonus} = {result.total}; "
                f"damage rolls {damage}; mitigated {mitigation}; final {result.final_damage}]"
            )
    except Exception:
        # Presentation preferences must never turn a valid attack into a failed one.
        pass
    return message


def _message_for_target(attacker: Any, target: Any, result: AttackResult) -> str:
    """Render the target-only view without hidden mechanical values."""
    name = attacker.get_display_name(target)
    if result.outcome is AttackOutcome.MISS:
        return f"{name} misses you."
    if result.final_damage == 0:
        return f"{name}'s blow against your {result.hit_location} is fully absorbed."
    verb = (
        "critically strikes" if result.outcome is AttackOutcome.CRITICAL else "strikes"
    )
    return f"{name} {verb} your {result.hit_location} for {result.final_damage} damage."


def _message_for_observer(
    attacker: Any, target: Any, observer: Any, result: AttackResult
) -> str:
    """Render the room-observer view with display-aware names."""
    attacker_name = attacker.get_display_name(observer)
    target_name = target.get_display_name(observer)
    if result.outcome is AttackOutcome.MISS:
        return f"{attacker_name} misses {target_name}."
    if result.final_damage == 0:
        return f"{attacker_name}'s blow against {target_name}'s {result.hit_location} is fully absorbed."
    verb = (
        "critically strikes" if result.outcome is AttackOutcome.CRITICAL else "strikes"
    )
    return f"{attacker_name} {verb} {target_name}'s {result.hit_location}."


def _is_character(value: Any) -> bool:
    """Avoid accepting rooms, items, and arbitrary test doubles as combatants."""
    from typeclasses.characters import Character

    return isinstance(value, Character)


def _is_player_character(character: Any) -> bool:
    """Use explicit persistent ownership, never transient session connectivity."""
    value = character.attributes.get("is_player_character")
    return True if value is None else bool(value)


def _is_protected(character: Any) -> bool:
    """Retain the historical helper while delegating to MOB-03 policy."""
    from systems.mobile_policy import is_protected

    return is_protected(character)


def _is_staff_immune(character: Any) -> bool:
    """Keep staff targets safe even before final PvP policy exists."""
    return bool(
        getattr(character, "is_superuser", False) or character.check_permstring("Admin")
    )


def _is_staff_override(character: Any) -> bool:
    """Allow staff to test and intervene without opening ordinary PvP."""
    return bool(
        getattr(character, "is_superuser", False) or character.check_permstring("Admin")
    )


def _has_explicit_attack_permission(attacker: Any, target: Any) -> bool:
    """Require a real attack lock, not the permissive default for NPC targets."""
    return bool(
        target.locks.get("attack") and target.access(attacker, "attack", default=False)
    )

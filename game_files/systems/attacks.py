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
from systems.combat import CombatActionResult
from systems.dice import roll, roll_damage_expression
from systems.equipment import HIT_LOCATIONS, DamageMitigation
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
    if not attacker.actions.check(ActionCategory.COMBAT).allowed:
        return AttackabilityDecision(False, "attacker_ineligible")
    if not target.actions.check(ActionCategory.COMBAT).allowed:
        return AttackabilityDecision(False, "target_ineligible")
    if target.stats.hp_current <= 0:
        return AttackabilityDecision(False, "target_defeated")
    if _is_protected(target):
        return AttackabilityDecision(False, "protected")
    if _is_staff_immune(target):
        return AttackabilityDecision(False, "staff_immune")
    if not target.access(attacker, "attack", default=True):
        return AttackabilityDecision(False, "access_denied")
    if _is_player_character(attacker) and _is_player_character(target):
        if not (
            _is_staff_override(attacker)
            or _has_explicit_attack_permission(attacker, target)
        ):
            return AttackabilityDecision(False, "pvp_denied")
    return AttackabilityDecision(True)


def resolve_basic_attack(
    attacker: Any,
    target: Any,
    event: PulseEvent | None = None,
    *,
    die_roller: Callable[[int], int] = roll,
    location_selector: HitLocationSelector = select_hit_location,
    emit_messages: bool = True,
) -> AttackResult:
    """Resolve exactly one revalidated attack and mutate target HP at most once."""
    decision = can_attack(attacker, target)
    attacker_id = getattr(attacker, "id", None)
    target_id = getattr(target, "id", None)
    if not decision.allowed:
        return AttackResult(
            reason=decision.reason, attacker_id=attacker_id, target_id=target_id
        )

    profile = attacker.stats.attack_profile()
    attack_rolls = (
        (die_roller(20), die_roller(20))
        if attacker.stats.has_untrained_armor
        else (die_roller(20),)
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20
        for value in attack_rolls
    ):
        raise ValueError("Attack roller returned an invalid d20 result.")
    die_roll = min(attack_rolls)
    total = die_roll + profile.attack_bonus
    armor_class = target.stats.armor_class
    if die_roll == 1:
        outcome = AttackOutcome.MISS
    elif die_roll == 20:
        outcome = AttackOutcome.CRITICAL
    elif total >= armor_class:
        outcome = AttackOutcome.HIT
    else:
        outcome = AttackOutcome.MISS

    common = {
        "accepted": True,
        "attacker_id": attacker.id,
        "target_id": target.id,
        "attack_name": profile.name,
        "attack_rolls": attack_rolls,
        "die_roll": die_roll,
        "attack_bonus": profile.attack_bonus,
        "total": total,
        "armor_class": armor_class,
        "outcome": outcome,
        "damage_type": profile.damage_type,
    }
    if outcome is AttackOutcome.MISS:
        result = AttackResult(acted=True, **common)
        if emit_messages:
            render_attack_result(attacker, target, result)
        return result

    location = location_selector(attacker, target)
    if location not in HIT_LOCATIONS:
        raise ValueError("Hit-location selector returned an unsupported location.")
    if profile.damage_dice:
        damage_roll = roll_damage_expression(
            profile.damage_dice,
            multiplier=2 if outcome is AttackOutcome.CRITICAL else 1,
            roller=die_roller,
        )
        damage_rolls, dice_total = damage_roll.rolls, damage_roll.total
    else:
        damage_rolls, dice_total = (), 0
    damage_total = max(0, dice_total + profile.damage_base + profile.damage_bonus)
    mitigation = target.stats.mitigate_damage(
        damage_total, location, profile.damage_type
    )
    resulting_hp = target.stats.take_damage(mitigation.final)
    result = AttackResult(
        acted=True,
        hit_location=location,
        damage_rolls=damage_rolls,
        damage_total=damage_total,
        mitigation=mitigation,
        final_damage=mitigation.final,
        resulting_hp=resulting_hp,
        remove_target=resulting_hp <= 0,
        **common,
    )
    if emit_messages:
        render_attack_result(attacker, target, result)
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
        return f"You miss {name}."
    if result.final_damage == 0:
        return f"Your blow against {name}'s {result.hit_location} is fully absorbed."
    verb = "critically strike" if result.outcome is AttackOutcome.CRITICAL else "strike"
    return (
        f"You {verb} {name}'s {result.hit_location} for {result.final_damage} damage."
    )


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
    """Honor the early protection seam used by future mobile policy."""
    return bool(character.db.protected or character.db.noncombatant)


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

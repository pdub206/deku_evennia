"""Queued physical combat actions for COMBAT-08.

Definitions are deliberately small registry entries.  Persistent encounter
storage contains only a stable action key plus primitive arguments; all object,
equipment, condition, and combat checks happen again when the pulse executes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from systems.attacks import (AttackOutcome, AttackResult, can_attack,
                             resolve_basic_attack)
from systems.character_stats import AttackProfile
from systems.combat import CombatActionResult, get_encounter_id, get_target
from systems.dice import RollResult, roll_check
from systems.effects import (EFFECT_REGISTRY, ApplyOutcome, EffectDefinition,
                             RemovalReason, StackingPolicy)
from systems.equipment import HIT_LOCATIONS
from systems.pulses import PulseEvent

PRONE_EFFECT_KEY = "combat.prone"
KICK_LOCATIONS = ("body", "left leg", "right leg")
KICK_LOCATION_WEIGHTS = MappingProxyType({"body": 6, "left leg": 1, "right leg": 1})
_SIZE_ORDER = {
    "tiny": 0,
    "small": 1,
    "medium": 2,
    "large": 3,
    "huge": 4,
    "gargantuan": 5,
}


@dataclass(frozen=True)
class TacticalActionResult(CombatActionResult):
    """Immutable evidence for a consumed tactical intent."""

    action: str = ""
    pulse: int | None = None
    accepted: bool = False
    reason: str = ""
    actor_id: int | None = None
    target_id: int | None = None
    attack: AttackResult | None = None
    attacker_roll: RollResult | None = None
    defender_roll: RollResult | None = None
    effect_applied: str | None = None
    retargeted: bool = False


TacticalHandler = Callable[
    [Any, Any, PulseEvent, Mapping[str, Any]], TacticalActionResult
]


class TacticalActionRegistry:
    """Code-owned action definitions keyed by stable persisted identifiers."""

    def __init__(self) -> None:
        self._handlers: dict[str, TacticalHandler] = {}

    def register(self, key: str, handler: TacticalHandler) -> None:
        if not key.replace("_", "").isalpha() or not callable(handler):
            raise ValueError(
                "Tactical actions need an alphabetic key and callable handler."
            )
        if key in self._handlers:
            raise ValueError(f"Tactical action '{key}' is already registered.")
        self._handlers[key] = handler

    def get(self, key: str) -> TacticalHandler | None:
        return self._handlers.get(key)


TACTICAL_ACTIONS = TacticalActionRegistry()


def resolve_combat_action(actor: Any, target: Any, event: PulseEvent) -> AttackResult:
    """Resolve the ordinary COMBAT-02 attack when no tactical intent exists."""
    return resolve_basic_attack(actor, target, event)


def validate_tactical_intent(
    action: str, target_id: int, arguments: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Build a normalized, serializable tactical intent or reject it safely."""
    if not isinstance(action, str) or TACTICAL_ACTIONS.get(action) is None:
        return None
    if isinstance(target_id, bool) or not isinstance(target_id, int) or target_id <= 0:
        return None
    if not isinstance(arguments, Mapping):
        return None
    intent: dict[str, Any] = {"kind": "tactical", "action": action, "target": target_id}
    if action == "aim":
        location = arguments.get("location")
        if not isinstance(location, str):
            return None
        location = location.strip().casefold()
        if location not in HIT_LOCATIONS:
            return None
        intent["location"] = location
    elif arguments:
        return None
    return intent


def valid_tactical_intent(intent: Any) -> bool:
    """Validate a persisted tactical record without resolving game objects."""
    if not isinstance(intent, Mapping) or intent.get("kind") != "tactical":
        return False
    action, target = intent.get("action"), intent.get("target")
    if not isinstance(action, str) or TACTICAL_ACTIONS.get(action) is None:
        return False
    arguments = {
        key: value
        for key, value in intent.items()
        if key not in {"kind", "action", "target"}
    }
    normalized = validate_tactical_intent(action, target, arguments)
    return normalized == dict(intent)


def execute_tactical_intent(
    actor: Any, _current_target: Any, event: PulseEvent, intent: Mapping[str, Any]
) -> TacticalActionResult:
    """Consume a stored action exactly once, revalidating it at execution."""
    action = str(intent.get("action", ""))
    target_id = intent.get("target")
    common = {
        "action": action,
        "pulse": event.sequence,
        "actor_id": getattr(actor, "id", None),
        "target_id": target_id,
    }
    if not valid_tactical_intent(intent):
        return TacticalActionResult(acted=True, reason="invalid_intent", **common)
    from systems.combat import _get_character

    target = _get_character(target_id)
    if target is None or get_encounter_id(actor) != get_encounter_id(target):
        return TacticalActionResult(acted=True, reason="invalid_target", **common)
    decision = can_attack(actor, target)
    if not decision.allowed:
        return TacticalActionResult(acted=True, reason=decision.reason, **common)
    handler = TACTICAL_ACTIONS.get(action)
    if handler is None:
        return TacticalActionResult(acted=True, reason="unknown_action", **common)
    return handler(actor, target, event, intent)


def clear_combat_conditions(character: Any) -> None:
    """Clear encounter-only effects whenever their owner leaves combat."""
    for effect in character.effects.all():
        if effect.key == PRONE_EFFECT_KEY:
            character.effects.remove(
                effect.instance_id, reason=RemovalReason.ADMIN, quiet=True
            )


def consume_prone_action(character: Any) -> bool:
    """Spend one ready action standing, and remove the non-stacking knockdown."""
    if not character.effects.has(PRONE_EFFECT_KEY):
        return False
    clear_combat_conditions(character)
    character.msg("You regain your footing instead of taking a combat action.")
    return True


def _aim(
    actor: Any, target: Any, event: PulseEvent, intent: Mapping[str, Any]
) -> TacticalActionResult:
    """Make one disadvantaged attack against the chosen canonical location."""
    location = intent["location"]
    attack = resolve_basic_attack(
        actor,
        target,
        event,
        has_disadvantage=True,
        location_selector=lambda *_: location,
    )
    return TacticalActionResult(
        acted=True,
        action="aim",
        pulse=event.sequence,
        accepted=attack.accepted,
        actor_id=actor.id,
        target_id=target.id,
        attack=attack,
        remove_target=attack.remove_target,
    )


def _backstab(
    actor: Any, target: Any, event: PulseEvent, intent: Mapping[str, Any]
) -> TacticalActionResult:
    """Apply the Rogue's once-per-round Sneak Attack through normal damage."""
    reason = _backstab_reason(actor, target, event)
    common = {
        "acted": True,
        "action": "backstab",
        "pulse": event.sequence,
        "actor_id": actor.id,
        "target_id": target.id,
    }
    if reason:
        return TacticalActionResult(reason=reason, **common)
    dice = f"{1 + (actor.stats.level - 1) // 2}d6"
    attack = resolve_basic_attack(
        actor, target, event, extra_damage_dice=dice, attack_name="backstab"
    )
    # A miss is an attempt but not a successfully applied Sneak Attack.
    if attack.outcome is not AttackOutcome.MISS:
        actor.db.combat_sneak_attack_round = event.sequence
    return TacticalActionResult(
        accepted=attack.accepted,
        attack=attack,
        remove_target=attack.remove_target,
        **common,
    )


def _backstab_reason(actor: Any, target: Any, event: PulseEvent) -> str:
    """Validate class, weapon, awareness, and per-round Sneak Attack rules."""
    if actor.attributes.get("char_class", "Fighter") != "Rogue":
        return "not_rogue"
    weapon = actor.equipment.wielded_weapon
    if weapon is None or not _is_finesse_weapon(weapon):
        return "finesse_weapon_required"
    if actor.attributes.get("combat_sneak_attack_round") == event.sequence:
        return "already_used_this_round"
    if actor.stats.has_untrained_armor:
        return "disadvantage"
    participant_count = _participant_count(actor)
    if participant_count <= 2:
        if not _hidden_from(actor, target):
            return "target_aware"
    elif get_target(target) is actor:
        return "target_focused_on_you"
    return ""


def _kick(
    actor: Any, target: Any, event: PulseEvent, intent: Mapping[str, Any]
) -> TacticalActionResult:
    """Strike with a Strength-based unarmed kick and a 150% next delay."""
    strength = actor.stats.ability_modifier("Strength")
    profile = AttackProfile(
        name="kick",
        ability="Strength",
        attack_bonus=strength + actor.stats.proficiency_bonus,
        damage_dice="1d4",
        damage_base=0,
        damage_bonus=strength,
        damage_type="bludgeoning",
        proficient=True,
    )
    attack = resolve_basic_attack(
        actor,
        target,
        event,
        profile=profile,
        attack_name="kick",
        location_selector=_select_kick_location,
    )
    return TacticalActionResult(
        acted=True,
        action="kick",
        pulse=event.sequence,
        accepted=attack.accepted,
        actor_id=actor.id,
        target_id=target.id,
        attack=attack,
        remove_target=attack.remove_target,
        delay_multiplier=1.5,
    )


def _bash(
    actor: Any, target: Any, event: PulseEvent, intent: Mapping[str, Any]
) -> TacticalActionResult:
    """Contest a shield bash; success applies one combat-only prone effect."""
    common = {
        "acted": True,
        "action": "bash",
        "pulse": event.sequence,
        "actor_id": actor.id,
        "target_id": target.id,
    }
    if actor.equipment.shield is None:
        return TacticalActionResult(reason="shield_required", **common)
    if _size_rank(target) > _size_rank(actor) + 1:
        return TacticalActionResult(reason="target_too_large", **common)
    attacker_roll = roll_check(actor.stats.skill_bonus("Athletics"), 0)
    defender_bonus = max(
        target.stats.skill_bonus("Athletics"), target.stats.skill_bonus("Acrobatics")
    )
    defender_roll = roll_check(defender_bonus, 0)
    if attacker_roll.total < defender_roll.total:
        return TacticalActionResult(
            reason="contest_failed",
            attacker_roll=attacker_roll,
            defender_roll=defender_roll,
            **common,
        )
    application = target.effects.add(PRONE_EFFECT_KEY, source=actor, quiet=True)
    applied = application.outcome in {ApplyOutcome.APPLIED, ApplyOutcome.REPLACED}
    return TacticalActionResult(
        accepted=True,
        reason="" if applied else "already_prone",
        attacker_roll=attacker_roll,
        defender_roll=defender_roll,
        effect_applied=PRONE_EFFECT_KEY if applied else None,
        **common,
    )


def _select_kick_location(actor: Any, target: Any) -> str:
    """Choose exclusively among the validated kick targets using shared dice."""
    from systems.dice import roll

    selected = roll(sum(KICK_LOCATION_WEIGHTS.values()))
    for location in KICK_LOCATIONS:
        selected -= KICK_LOCATION_WEIGHTS[location]
        if selected <= 0:
            return location
    raise RuntimeError("Valid kick weights did not choose a location.")


def _is_finesse_weapon(weapon: Any) -> bool:
    """Accept the existing compact weapon-property forms used by builders."""
    if bool(weapon.attributes.get("finesse")):
        return True
    properties = weapon.attributes.get(
        "weapon_properties", weapon.attributes.get("properties", ())
    )
    return isinstance(properties, (list, tuple, set)) and any(
        str(value).casefold() == "finesse" for value in properties
    )


def _hidden_from(actor: Any, target: Any) -> bool:
    """Consume ADV-04's canonical hidden condition without duplicating stealth."""
    return actor.effects.has_condition("hidden")


def _participant_count(actor: Any) -> int:
    """Read only the current encounter's live participant count."""
    from systems.combat import _read_state

    encounter_id = get_encounter_id(actor)
    if encounter_id is None:
        return 0
    return len(
        _read_state()["encounters"].get(str(encounter_id), {}).get("participants", {})
    )


def _size_rank(character: Any) -> int:
    """Map supported builder size labels to a conservative Medium default."""
    return _SIZE_ORDER.get(
        str(character.attributes.get("size", "Medium")).casefold(), 2
    )


def _register_defaults() -> None:
    """Register reload-safe built-in tactical definitions once per process."""
    if EFFECT_REGISTRY.get(PRONE_EFFECT_KEY) is None:
        EFFECT_REGISTRY.register(
            EffectDefinition(
                key=PRONE_EFFECT_KEY,
                name="Prone",
                stacking=StackingPolicy.REJECT,
                conditions=frozenset({"prone"}),
            )
        )
    for key, handler in {
        "aim": _aim,
        "backstab": _backstab,
        "bash": _bash,
        "kick": _kick,
    }.items():
        if TACTICAL_ACTIONS.get(key) is None:
            TACTICAL_ACTIONS.register(key, handler)


_register_defaults()

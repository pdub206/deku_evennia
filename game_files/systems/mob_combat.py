"""MOB-02 combat decisions built on the shared encounter and attack services.

NPC prototypes hold a small, JSON-like combat profile.  This module never
persists code or object references and deliberately delegates encounter,
attack, tactical-intent, and flee execution to their owning systems.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from systems.attacks import can_attack, resolve_basic_attack
from systems.combat import (CombatActionResult, change_target,
                            get_encounter_id, is_fighting, schedule_flee,
                            schedule_tactical_action, start_fight)
from systems.combat_movement import choose_flee_exit
from systems.injury import InjuryError, InjuryState, injury_record
from systems.mobile_policy import (MOBILE_POLICY_ATTRIBUTE, MobilePolicyError,
                                   mobile_policy)
from systems.pulses import PulseEvent
from systems.tactical_combat import (execute_tactical_intent,
                                     validate_tactical_intent)

MOB_COMBAT_PROFILE_ATTRIBUTE = "mob_combat_profile"
MOB_COMBAT_STATE_ATTRIBUTE = "mob_combat_state"
MOB_COMBAT_PROFILE_VERSION = 1
MOB_COMBAT_STATE_VERSION = 1
TARGET_CURRENT = "current"
TARGET_LOWEST_ID = "lowest_id"
TARGET_POLICIES = frozenset({TARGET_CURRENT, TARGET_LOWEST_ID})


class MobCombatError(ValueError):
    """An NPC combat profile or its runtime state is unsafe to use."""


TacticalChooser = Callable[[tuple[dict[str, Any], ...]], dict[str, Any] | None]


def default_combat_profile() -> dict[str, Any]:
    """Return the safe, basic-attack-only profile for new NPC templates."""
    return {
        "version": MOB_COMBAT_PROFILE_VERSION,
        "target_policy": TARGET_CURRENT,
        "tactics": [],
        "wimpy": 0,
    }


def validate_combat_profile(profile: Any) -> dict[str, Any]:
    """Detach and validate all primitive prototype combat configuration."""
    if not isinstance(profile, Mapping) or set(profile) != {
        "version",
        "target_policy",
        "tactics",
        "wimpy",
    }:
        raise MobCombatError("NPC combat profile has an invalid shape.")
    if profile["version"] != MOB_COMBAT_PROFILE_VERSION:
        raise MobCombatError("NPC combat profile has an unsupported version.")
    policy = profile["target_policy"]
    if not isinstance(policy, str) or policy not in TARGET_POLICIES:
        raise MobCombatError("NPC combat profile has an invalid target policy.")
    wimpy = profile["wimpy"]
    if isinstance(wimpy, bool) or not isinstance(wimpy, int) or not 0 <= wimpy <= 90:
        raise MobCombatError("NPC wimpy must be a whole percentage from 0 to 90.")
    tactics = profile["tactics"]
    # Evennia's Attribute serializer may round-trip a stored JSON list as a
    # tuple. Accept that representation while always returning a fresh list.
    if isinstance(tactics, (str, bytes)) or not isinstance(tactics, Sequence):
        raise MobCombatError("NPC tactics must be a list.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tactic in tactics:
        if not isinstance(tactic, Mapping) or set(tactic) != {
            "action",
            "weight",
            "arguments",
            "cooldown",
        }:
            raise MobCombatError("NPC tactic has an invalid shape.")
        action, weight, cooldown = (
            tactic["action"],
            tactic["weight"],
            tactic["cooldown"],
        )
        if (
            not isinstance(action, str)
            or not action
            or action in seen
            or isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight < 1
            or isinstance(cooldown, bool)
            or not isinstance(cooldown, int)
            or cooldown < 0
            or not isinstance(tactic["arguments"], Mapping)
        ):
            raise MobCombatError("NPC tactic has invalid values.")
        # The tactical registry is code-owned. A profile may not nominate an
        # unknown action, arguments its schema does not accept, or a callable.
        if validate_tactical_intent(action, 1, tactic["arguments"]) is None:
            raise MobCombatError("NPC tactic is unavailable or malformed.")
        seen.add(action)
        normalized.append(
            {
                "action": action,
                "weight": weight,
                "arguments": _primitive_copy(tactic["arguments"]),
                "cooldown": cooldown,
            }
        )
    return {
        "version": MOB_COMBAT_PROFILE_VERSION,
        "target_policy": policy,
        "tactics": normalized,
        "wimpy": wimpy,
    }


def combat_profile(npc: Any) -> dict[str, Any]:
    """Read a validated detached profile, failing closed for malformed data."""
    raw = npc.attributes.get(MOB_COMBAT_PROFILE_ATTRIBUTE)
    return default_combat_profile() if raw is None else validate_combat_profile(raw)


def set_combat_profile(npc: Any, profile: Any) -> dict[str, Any]:
    """Persist one validated profile and reset only MOB-02's runtime latches."""
    normalized = validate_combat_profile(profile)
    npc.attributes.add(MOB_COMBAT_PROFILE_ATTRIBUTE, normalized)
    npc.attributes.add(MOB_COMBAT_STATE_ATTRIBUTE, _initial_state())
    return deepcopy(normalized)


def retaliate(npc: Any, attacker: Any) -> bool:
    """Start exactly one ordinary retaliation encounter after hostile damage."""
    if _is_player_character(npc) or is_fighting(npc):
        return False
    try:
        combat_profile(npc)
        if injury_record(npc).state is not InjuryState.CONSCIOUS:
            return False
    except (MobCombatError, InjuryError, AttributeError, TypeError):
        return False
    if not can_attack(npc, attacker).allowed:
        return False
    return start_fight(npc, attacker).accepted


def reconcile_mob_wimpy(npc: Any, previous_hp: int, current_hp: int) -> None:
    """Latch one NPC flee attempt per downward threshold crossing.

    An unavailable route still latches the crossing. This avoids retrying an
    impossible flee on every later combat action; healing above the boundary
    rearms the NPC for a future crossing.
    """
    if _is_player_character(npc):
        return
    try:
        # MOB-03 owns new NPC wimpy configuration.  Old live copies without a
        # policy retain their MOB-02 value until a builder next edits them.
        profile = combat_profile(npc)
        state = _state(npc)
    except (MobCombatError, AttributeError, TypeError):
        return
    try:
        threshold = (
            profile["wimpy"]
            if npc.attributes.get(MOBILE_POLICY_ATTRIBUTE) is None
            else mobile_policy(npc)["wimpy"]
        )
    except (MobilePolicyError, AttributeError, TypeError):
        return
    if not threshold:
        return
    boundary = npc.stats.hp_max * threshold / 100
    if current_hp > boundary:
        if state["wimpy_triggered"]:
            state["wimpy_triggered"] = False
            _write_state(npc, state)
        return
    if (
        previous_hp <= boundary
        or current_hp > boundary
        or state["wimpy_triggered"]
        or not is_fighting(npc)
    ):
        return
    try:
        conscious = injury_record(npc).state is InjuryState.CONSCIOUS
    except InjuryError:
        conscious = False
    if not conscious:
        return
    state["wimpy_triggered"] = True
    _write_state(npc, state)
    route = choose_flee_exit(npc)
    if route.allowed:
        schedule_flee(npc, route.exit.id)


def resolve_mob_combat_action(
    actor: Any,
    current_target: Any,
    event: PulseEvent,
    *,
    tactic_chooser: TacticalChooser | None = None,
) -> CombatActionResult:
    """Choose one legal NPC action using the existing combat action clock."""
    if _is_player_character(actor):
        return resolve_basic_attack(actor, current_target, event)
    try:
        profile = combat_profile(actor)
    except (MobCombatError, AttributeError, TypeError):
        return CombatActionResult(acted=True)
    target = select_combat_target(actor, current_target, profile)
    if target is None:
        return CombatActionResult(acted=True)
    if target is not current_target:
        change_target(actor, target)
    tactic = select_tactical_action(actor, profile, event.sequence, tactic_chooser)
    if tactic is not None:
        result = _execute_tactical(actor, target, event, tactic)
        if result.accepted:
            _set_tactic_ready(
                actor, tactic["action"], event.sequence + tactic["cooldown"]
            )
            return result
    return resolve_basic_attack(actor, target, event)


def select_combat_target(
    actor: Any, current_target: Any, profile: Mapping[str, Any]
) -> Any | None:
    """Choose a canonically attackable hostile participant in stable order."""
    candidates = _eligible_targets(actor)
    if not candidates:
        return None
    if profile["target_policy"] == TARGET_CURRENT and current_target in candidates:
        return current_target
    return candidates[0]


def select_tactical_action(
    actor: Any,
    profile: Mapping[str, Any],
    token: int,
    chooser: TacticalChooser | None = None,
) -> dict[str, Any] | None:
    """Choose a ready allowed tactic, deterministically unless injected."""
    state = _state(actor)
    candidates = tuple(
        tactic
        for tactic in profile["tactics"]
        if state["tactic_ready"].get(tactic["action"], 0) <= token
    )
    if not candidates:
        return None
    if chooser is not None:
        selected = chooser(deepcopy(candidates))
        return deepcopy(selected) if selected in candidates else None
    return deepcopy(
        sorted(candidates, key=lambda value: (-value["weight"], value["action"]))[0]
    )


def _execute_tactical(
    actor: Any, target: Any, event: PulseEvent, tactic: Mapping[str, Any]
) -> CombatActionResult:
    """Schedule through COMBAT-08, then consume the chosen ready action once."""
    scheduled = schedule_tactical_action(
        actor, tactic["action"], target, **tactic["arguments"]
    )
    if not scheduled.accepted:
        return CombatActionResult()
    intent = validate_tactical_intent(tactic["action"], target.id, tactic["arguments"])
    if intent is None:
        return CombatActionResult()
    # The intent API supplies replacement-safe validation and serializable
    # storage. The actor is already ready, so execute it now rather than
    # creating a competing AI timer or delaying this one combat action.
    result = execute_tactical_intent(actor, target, event, intent)
    _clear_scheduled_tactic(actor, intent)
    return result


def _eligible_targets(actor: Any) -> tuple[Any, ...]:
    """Return live opposing encounter members that pass canonical attack rules."""
    encounter_id = get_encounter_id(actor)
    if encounter_id is None:
        return ()
    from systems.combat import _get_character, _participant_sides, _read_state

    encounter = _read_state()["encounters"].get(str(encounter_id))
    if encounter is None:
        return ()
    sides = _participant_sides(encounter)
    candidates = []
    for candidate_id, side in sides.items():
        if side == sides.get(actor.id):
            continue
        candidate = _get_character(candidate_id)
        if candidate is not None and can_attack(actor, candidate).allowed:
            candidates.append(candidate)
    return tuple(sorted(candidates, key=lambda candidate: candidate.id))


def _initial_state() -> dict[str, Any]:
    """Return primitive durable state for cooldowns and one flee latch."""
    return {
        "version": MOB_COMBAT_STATE_VERSION,
        "wimpy_triggered": False,
        "tactic_ready": {},
    }


def _state(npc: Any) -> dict[str, Any]:
    """Read validated runtime state, repairing only an absent initial record."""
    raw = npc.attributes.get(MOB_COMBAT_STATE_ATTRIBUTE)
    if raw is None:
        state = _initial_state()
        _write_state(npc, state)
        return state
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "wimpy_triggered", "tactic_ready"}
        or raw.get("version") != MOB_COMBAT_STATE_VERSION
        or not isinstance(raw.get("wimpy_triggered"), bool)
        or not isinstance(raw.get("tactic_ready"), Mapping)
        or not all(
            isinstance(key, str)
            and key
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for key, value in raw["tactic_ready"].items()
        )
    ):
        raise MobCombatError("NPC combat runtime state is invalid.")
    return {
        "version": MOB_COMBAT_STATE_VERSION,
        "wimpy_triggered": raw["wimpy_triggered"],
        "tactic_ready": dict(raw["tactic_ready"]),
    }


def _write_state(npc: Any, state: Mapping[str, Any]) -> None:
    """Persist only validated primitive runtime state."""
    npc.attributes.add(MOB_COMBAT_STATE_ATTRIBUTE, _state_copy(state))


def _state_copy(state: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a state-shaped mapping before it reaches persistent storage."""
    # Use a detached fake read shape to keep writes held to the same contract.
    if not isinstance(state, Mapping):
        raise MobCombatError("NPC combat runtime state is invalid.")
    copied = {
        "version": state.get("version"),
        "wimpy_triggered": state.get("wimpy_triggered"),
        "tactic_ready": (
            dict(state.get("tactic_ready", {}))
            if isinstance(state.get("tactic_ready"), Mapping)
            else state.get("tactic_ready")
        ),
    }
    if (
        copied["version"] != MOB_COMBAT_STATE_VERSION
        or not isinstance(copied["wimpy_triggered"], bool)
        or not isinstance(copied["tactic_ready"], dict)
        or not all(
            isinstance(key, str)
            and key
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for key, value in copied["tactic_ready"].items()
        )
    ):
        raise MobCombatError("NPC combat runtime state is invalid.")
    return copied


def _set_tactic_ready(npc: Any, action: str, token: int) -> None:
    """Record a selected tactic's next eligible combat token."""
    state = _state(npc)
    state["tactic_ready"][action] = token
    _write_state(npc, state)


def _clear_scheduled_tactic(actor: Any, intent: Mapping[str, Any]) -> None:
    """Clear the transient queued copy after this ready action consumes it."""
    from systems.combat import _read_state, _write_state

    state = _read_state()
    encounter_id = get_encounter_id(actor)
    if encounter_id is None:
        return
    record = (
        state["encounters"]
        .get(str(encounter_id), {})
        .get("participants", {})
        .get(str(actor.id))
    )
    if record is not None and record.get("pending_intent") == dict(intent):
        record["pending_intent"] = None
        _write_state(state)


def _primitive_copy(value: Any) -> Any:
    """Reject prototype callables and live objects while detaching primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_primitive_copy(item) for item in value]
    if isinstance(value, Mapping) and all(
        isinstance(key, str) and key for key in value
    ):
        return {key: _primitive_copy(item) for key, item in value.items()}
    raise MobCombatError("NPC combat data must contain primitive values only.")


def _is_player_character(character: Any) -> bool:
    """Use the durable identity marker rather than account/session presence."""
    return getattr(getattr(character, "db", None), "is_player_character", None) is True

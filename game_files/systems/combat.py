"""Persistent encounter orchestration for the global combat pulse.

COMBAT-01 deliberately owns no attack rules.  It keeps one serializable,
room-scoped encounter registry, decides whose action clock is due, and calls a
replaceable resolver.  Combat rules added by later milestones therefore do not
need their own timers or membership bookkeeping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from evennia.server.models import ServerConfig
from evennia.utils import logger
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    LifecycleConsumer,
    LifecycleError,
    ServerLifecycleEvent,
    ServerTransitionPhase,
    UnavailabilityCause,
    register_lifecycle_consumer,
    unregister_lifecycle_consumer,
)
from systems.pulses import PulseEvent, PulseLane

COMBAT_CONFIG_KEY = "combat_registry"
COMBAT_STATE_VERSION = 1
COMBAT_LIFECYCLE_KEY = "combat"


class CombatError(ValueError):
    """A requested combat operation cannot safely be performed."""


@dataclass(frozen=True)
class CombatOperationResult:
    """The safe, player-neutral result of a registry operation."""

    accepted: bool
    changed: bool
    encounter_id: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class CombatActionResult:
    """Structured result returned by the current combat action resolver."""

    acted: bool = False
    remove_target: bool = False
    delay_multiplier: float | None = None


@dataclass(frozen=True)
class CombatPulseResult:
    """Summary of one idempotent combat-pulse attempt."""

    processed: bool
    encounters: int = 0
    actions: int = 0
    failures: int = 0


CombatActionHook = Callable[[Any, Any, PulseEvent], CombatActionResult]


def _no_action(actor: Any, target: Any, event: PulseEvent) -> CombatActionResult:
    """Provide COMBAT-01's intentionally rule-free default action."""
    return CombatActionResult()


_action_hook: CombatActionHook = _no_action


def set_combat_action_hook(hook: CombatActionHook | None) -> None:
    """Install a process-local resolver; ``None`` restores the no-op default."""
    global _action_hook
    if hook is not None and not callable(hook):
        raise CombatError("A combat action hook must be callable.")
    _action_hook = _no_action if hook is None else hook


def start_fight(actor: Any, target: Any) -> CombatOperationResult:
    """Start, join, merge, or retarget a same-room encounter idempotently."""
    reason = _valid_pair_reason(actor, target)
    if reason:
        return CombatOperationResult(False, False, reason=reason)

    state = _read_state()
    _repair_state(state)
    actor_id, target_id = actor.id, target.id
    actor_encounter = _participant_encounter(state, actor_id)
    target_encounter = _participant_encounter(state, target_id)

    if actor_encounter is not None and target_encounter is not None:
        if actor_encounter == target_encounter:
            encounter = state["encounters"][str(actor_encounter)]
            if not _can_target(encounter, actor_id, target_id):
                return CombatOperationResult(
                    False, False, reason="target is not an opposing participant"
                )
            changed = _set_target(state, actor_encounter, actor_id, target_id)
            _write_state(state)
            _refresh_prompts(actor, target)
            return CombatOperationResult(True, changed, actor_encounter)
        encounter_id = _merge_encounters(
            state, actor_encounter, target_encounter, actor_id, target_id
        )
    elif actor_encounter is not None:
        encounter_id = actor_encounter
        _add_participant(
            state,
            encounter_id,
            target_id,
            _opposing_side(state["encounters"][str(encounter_id)], actor_id),
        )
    elif target_encounter is not None:
        encounter_id = target_encounter
        _add_participant(
            state,
            encounter_id,
            actor_id,
            _opposing_side(state["encounters"][str(encounter_id)], target_id),
        )
    else:
        encounter_id = _create_encounter(state, actor.location.id, actor_id, target_id)

    _set_target(state, encounter_id, actor_id, target_id)
    _set_target(state, encounter_id, target_id, actor_id)
    _repair_state(state)
    _write_state(state)
    _refresh_prompts(actor, target)
    return CombatOperationResult(True, True, encounter_id)


def join_fight(actor: Any, target: Any) -> CombatOperationResult:
    """Join target's encounter, using the same validation and merge policy."""
    return start_fight(actor, target)


def change_target(actor: Any, target: Any) -> CombatOperationResult:
    """Retarget within actor's existing encounter without implicitly joining it."""
    reason = _valid_pair_reason(actor, target)
    if reason:
        return CombatOperationResult(False, False, reason=reason)
    state = _read_state()
    _repair_state(state)
    encounter_id = _participant_encounter(state, actor.id)
    if encounter_id is None:
        return CombatOperationResult(False, False, reason="actor is not fighting")
    encounter = state["encounters"][str(encounter_id)]
    if not _can_target(encounter, actor.id, target.id):
        return CombatOperationResult(False, False, reason="target is not in this fight")
    changed = _set_target(state, encounter_id, actor.id, target.id)
    _write_state(state)
    _refresh_prompts(actor, target)
    return CombatOperationResult(True, changed, encounter_id)


def leave_fight(actor: Any) -> CombatOperationResult:
    """Remove one participant and repair every affected target reference."""
    actor_id = _object_id(actor)
    if actor_id is None:
        return CombatOperationResult(False, False, reason="invalid participant")
    state = _read_state()
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return CombatOperationResult(True, False)
    participants = tuple(state["encounters"][str(encounter_id)]["participants"])
    _remove_participant(state, encounter_id, actor_id)
    _repair_state(state)
    _write_state(state)
    _refresh_prompts(
        *(_get_character(int(participant)) for participant in participants)
    )
    return CombatOperationResult(True, True, encounter_id)


def stop_fight(actor: Any) -> CombatOperationResult:
    """End one actor's participation; retained as the narrow stop API."""
    return leave_fight(actor)


def stop_encounter(encounter_id: int) -> CombatOperationResult:
    """Dissolve one encounter, primarily for staff recovery and future rules."""
    if isinstance(encounter_id, bool) or not isinstance(encounter_id, int):
        return CombatOperationResult(False, False, reason="invalid encounter")
    state = _read_state()
    if str(encounter_id) not in state["encounters"]:
        return CombatOperationResult(True, False, encounter_id)
    participants = tuple(state["encounters"][str(encounter_id)]["participants"])
    _clear_encounter_conditions(state["encounters"][str(encounter_id)])
    del state["encounters"][str(encounter_id)]
    _write_state(state)
    _refresh_prompts(*(_get_character(int(actor_id)) for actor_id in participants))
    return CombatOperationResult(True, True, encounter_id)


def is_fighting(actor: Any) -> bool:
    """Return whether actor currently has valid membership in an encounter."""
    actor_id = _object_id(actor)
    if actor_id is None:
        return False
    state = _read_state()
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return False
    encounter = state["encounters"].get(str(encounter_id))
    if encounter is None or len(encounter["participants"]) < 2:
        return False
    record = encounter["participants"].get(str(actor_id))
    target = _get_character(record["target"]) if record else None
    return target is not None and _valid_encounter_target(actor, target, encounter)


def get_target(actor: Any) -> Any | None:
    """Return actor's currently valid target, otherwise ``None``."""
    actor_id = _object_id(actor)
    if actor_id is None:
        return None
    state = _read_state()
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return None
    encounter = state["encounters"][str(encounter_id)]
    participant = encounter["participants"].get(str(actor_id))
    target = _get_character(participant["target"]) if participant else None
    if target is None or not _valid_encounter_target(actor, target, encounter):
        return None
    return target


def get_encounter_id(actor: Any) -> int | None:
    """Return an actor's current encounter identity without changing combat."""
    actor_id = _object_id(actor)
    if actor_id is None:
        return None
    state = _read_state()
    return _participant_encounter(state, actor_id)


def schedule_flee(actor: Any, exit_id: int) -> CombatOperationResult:
    """Persist one replacement-safe flee intent for actor's next ready action."""
    if not _positive_int(exit_id):
        return CombatOperationResult(False, False, reason="invalid_exit")
    actor_id = _object_id(actor)
    if actor_id is None:
        return CombatOperationResult(False, False, reason="invalid_participant")
    state = _read_state()
    _repair_state(state)
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return CombatOperationResult(False, False, reason="not_fighting")
    record = state["encounters"][str(encounter_id)]["participants"][str(actor_id)]
    intent = {"kind": "flee", "exit": exit_id}
    changed = record.get("pending_intent") != intent
    record["pending_intent"] = intent
    _write_state(state)
    _refresh_prompts(actor)
    return CombatOperationResult(True, changed, encounter_id)


def schedule_stabilization(actor: Any, target: Any) -> CombatOperationResult:
    """Queue one COMBAT-04 Medicine attempt for the actor's next action."""
    actor_id, target_id = _object_id(actor), _object_id(target)
    if actor_id is None or target_id is None:
        return CombatOperationResult(False, False, reason="invalid_participant")
    state = _read_state()
    _repair_state(state)
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return CombatOperationResult(False, False, reason="not_fighting")
    intent = {"kind": "stabilize", "target": target_id}
    record = state["encounters"][str(encounter_id)]["participants"][str(actor_id)]
    changed = record.get("pending_intent") != intent
    record["pending_intent"] = intent
    _write_state(state)
    _refresh_prompts(actor)
    return CombatOperationResult(True, changed, encounter_id)


def schedule_tactical_action(
    actor: Any, action: str, target: Any, **arguments: Any
) -> CombatOperationResult:
    """Queue one registered tactical action using only stable primitive data."""
    from systems.tactical_combat import validate_tactical_intent

    actor_id, target_id = _object_id(actor), _object_id(target)
    if actor_id is None or target_id is None:
        return CombatOperationResult(False, False, reason="invalid_participant")
    intent = validate_tactical_intent(action, target_id, arguments)
    if intent is None:
        return CombatOperationResult(False, False, reason="invalid_action")
    state = _read_state()
    _repair_state(state)
    encounter_id = _participant_encounter(state, actor_id)
    if encounter_id is None:
        return CombatOperationResult(False, False, reason="not_fighting")
    encounter = state["encounters"][str(encounter_id)]
    if not _can_target(encounter, actor_id, target_id):
        return CombatOperationResult(False, False, reason="invalid_target")
    record = encounter["participants"][str(actor_id)]
    changed = record.get("pending_intent") != intent
    record["pending_intent"] = intent
    _write_state(state)
    _refresh_prompts(actor)
    return CombatOperationResult(True, changed, encounter_id)


@dataclass(frozen=True)
class CombatRetargetResult:
    """The low-level, atomic result of a future rescue attempt."""

    accepted: bool
    changed: bool
    reason: str = ""
    encounter_id: int | None = None
    attacker_roll: Any | None = None
    defender_roll: Any | None = None


def rescue_retarget(rescuer: Any, protected: Any, enemy: Any) -> CombatRetargetResult:
    """Contest one enemy's focus without changing encounter membership.

    GROUP-04 owns player-facing selection and eligibility.  This primitive only
    guarantees that all three live participants remain in the same encounter.
    """
    from systems.dice import roll_check

    ids = tuple(_object_id(value) for value in (rescuer, protected, enemy))
    if any(value is None for value in ids) or len(set(ids)) != 3:
        return CombatRetargetResult(False, False, "invalid_participant")
    state = _read_state()
    _repair_state(state)
    encounter_ids = tuple(_participant_encounter(state, value) for value in ids)
    if None in encounter_ids or len(set(encounter_ids)) != 1:
        return CombatRetargetResult(False, False, "not_same_encounter")
    encounter_id = encounter_ids[0]
    encounter = state["encounters"][str(encounter_id)]
    if not all(
        _participant_is_valid(value, encounter) for value in (rescuer, protected, enemy)
    ):
        return CombatRetargetResult(False, False, "invalid_participant", encounter_id)
    sides = _participant_sides(encounter)
    if sides[rescuer.id] != sides[protected.id] or sides[rescuer.id] == sides[enemy.id]:
        return CombatRetargetResult(False, False, "invalid_participant", encounter_id)
    if encounter["participants"][str(enemy.id)]["target"] != protected.id:
        return CombatRetargetResult(
            False, False, "enemy_not_targeting_protected", encounter_id
        )
    attacker_roll = roll_check(rescuer.stats.skill_bonus("Athletics"), 0)
    defense_bonus = max(
        enemy.stats.skill_bonus("Athletics"), enemy.stats.skill_bonus("Acrobatics")
    )
    defender_roll = roll_check(defense_bonus, 0)
    if attacker_roll.total < defender_roll.total:
        return CombatRetargetResult(
            True, False, "contest_failed", encounter_id, attacker_roll, defender_roll
        )
    _set_target(state, encounter_id, enemy.id, rescuer.id)
    _write_state(state)
    return CombatRetargetResult(
        True, True, "", encounter_id, attacker_roll, defender_roll
    )


def process_combat_pulse(event: PulseEvent) -> CombatPulseResult:
    """Consume one combat token before isolated, deterministic due actions."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.COMBAT:
        raise CombatError("Combat processing requires a combat pulse event.")
    state = _read_state()
    if event.sequence <= state["last_pulse"]:
        return CombatPulseResult(False)

    # Persist first: retries can skip a round but can never replay an action.
    state["last_pulse"] = event.sequence
    _repair_state(state)
    _write_state(state)

    actions = failures = 0
    encounter_count = len(state["encounters"])
    for encounter_id in sorted(map(int, state["encounters"])):
        try:
            actions_added, failures_added = _process_encounter(
                state, encounter_id, event
            )
        except Exception:
            failures += 1
            logger.log_trace(
                f"Combat encounter {encounter_id} failed at pulse {event.sequence}."
            )
            continue
        actions += actions_added
        failures += failures_added
    _repair_state(state)
    _write_state(state)
    return CombatPulseResult(True, encounter_count, actions, failures)


def recover_combat_state() -> None:
    """Validate persisted membership after a lifecycle recovery exactly safely."""
    state = _read_state()
    _repair_state(state)
    _write_state(state)


def handle_departure(actor: Any) -> CombatOperationResult:
    """Remove a moved, extracted, disconnected, or OOC actor idempotently."""
    return leave_fight(actor)


def _process_encounter(
    state: dict[str, Any], encounter_id: int, event: PulseEvent
) -> tuple[int, int]:
    """Run each due actor once, revalidating shared state between actions."""
    _repair_state(state)
    encounter = state["encounters"].get(str(encounter_id))
    if encounter is None:
        return 0, 0
    due_ids = sorted(
        int(actor_id)
        for actor_id, record in encounter["participants"].items()
        if record["ready_at"] <= event.sequence
    )
    actions = failures = 0
    for actor_id in due_ids:
        _repair_state(state)
        encounter = state["encounters"].get(str(encounter_id))
        if encounter is None:
            break
        record = encounter["participants"].get(str(actor_id))
        if record is None or record["ready_at"] > event.sequence:
            continue
        actor = _get_character(actor_id)
        target = _get_character(record["target"])
        if (
            actor is None
            or target is None
            or not _valid_encounter_target(actor, target, encounter)
        ):
            _repair_state(state)
            continue
        try:
            from systems.tactical_combat import consume_prone_action

            prone_action = consume_prone_action(actor)
        except Exception:
            failures += 1
            logger.log_trace(
                f"Combat condition for actor #{actor_id} failed at pulse {event.sequence}."
            )
            continue
        # Consume this readiness before invoking extensible code.
        record["ready_at"] = event.sequence + _combat_delay(actor)
        intent = record.get("pending_intent")
        record["pending_intent"] = None
        _write_state(state)
        _refresh_prompts(actor)
        if prone_action:
            actions += 1
            continue
        if intent is not None:
            try:
                if intent["kind"] == "flee":
                    from systems.combat_movement import execute_flee_intent

                    execute_flee_intent(actor, intent)
                elif intent["kind"] == "stabilize":
                    from systems.injury import attempt_stabilization

                    target = _get_character(intent["target"])
                    if target is None:
                        raise CombatError("Stabilization target is missing.")
                    attempt_stabilization(actor, target)
                else:
                    from systems.tactical_combat import execute_tactical_intent

                    result = execute_tactical_intent(actor, target, event, intent)
                    if result.delay_multiplier is not None:
                        record["ready_at"] = event.sequence + (
                            _combat_delay(actor) * result.delay_multiplier
                        )
            except Exception:
                failures += 1
                logger.log_trace(
                    f"Combat intent for actor #{actor_id} failed at pulse {event.sequence}."
                )
            else:
                actions += 1
            continue
        try:
            result = _action_hook(actor, target, event)
            if not isinstance(result, CombatActionResult):
                raise CombatError("Combat action hooks must return CombatActionResult.")
        except Exception:
            failures += 1
            logger.log_trace(
                f"Combat actor #{actor_id} failed at pulse {event.sequence}."
            )
        else:
            actions += 1
            if result.remove_target:
                _remove_participant(state, encounter_id, target.id)
                _repair_state(state)
                _write_state(state)
                _refresh_prompts(actor, target)
    return actions, failures


def _combat_delay(actor: Any) -> float:
    """Apply RULES-01's cadence contract with a conservative settings default."""
    base_delay = getattr(settings, "GAME_COMBAT_BASE_DELAY", 1.0)
    try:
        delay = float(actor.stats.combat_delay(base_delay))
    except Exception:
        logger.log_trace(
            f"Combat cadence failed for object #{actor.id}; using 1 pulse."
        )
        return 1.0
    return max(0.001, delay)


def _initial_state() -> dict[str, Any]:
    """Return an empty registry containing only persistent primitives."""
    return {
        "version": COMBAT_STATE_VERSION,
        "next_id": 1,
        "last_pulse": 0,
        "encounters": {},
    }


def _read_state() -> dict[str, Any]:
    """Read a detached, validated registry or fail closed to an empty one."""
    raw = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
    if raw is None:
        return _initial_state()
    if not _valid_state(raw):
        logger.log_err("Malformed combat registry; resetting it safely.")
        return _initial_state()
    return {
        "version": COMBAT_STATE_VERSION,
        "next_id": raw["next_id"],
        "last_pulse": raw["last_pulse"],
        "encounters": {
            str(encounter_id): {
                "room": encounter["room"],
                "participants": {
                    str(actor_id): {
                        "target": participant["target"],
                        "ready_at": participant["ready_at"],
                        "side": _participant_sides(encounter)[int(actor_id)],
                        "pending_intent": participant.get("pending_intent"),
                    }
                    for actor_id, participant in encounter["participants"].items()
                },
            }
            for encounter_id, encounter in raw["encounters"].items()
        },
    }


def _write_state(state: dict[str, Any]) -> None:
    """Persist a registry only after it has been normalized to primitives."""
    ServerConfig.objects.conf(COMBAT_CONFIG_KEY, value=state)


def _valid_state(state: Any) -> bool:
    """Validate storage shape before it can influence combat membership."""
    if not isinstance(state, Mapping) or state.get("version") != COMBAT_STATE_VERSION:
        return False
    if not _positive_int(state.get("next_id")) or not _nonnegative_int(
        state.get("last_pulse")
    ):
        return False
    encounters = state.get("encounters")
    if not isinstance(encounters, Mapping):
        return False
    for encounter_id, encounter in encounters.items():
        if not _positive_int_string(encounter_id) or not isinstance(encounter, Mapping):
            return False
        if not _positive_int(encounter.get("room")):
            return False
        participants = encounter.get("participants")
        if not isinstance(participants, Mapping):
            return False
        for actor_id, participant in participants.items():
            if not _positive_int_string(actor_id) or not isinstance(
                participant, Mapping
            ):
                return False
            if set(participant) not in (
                {"target", "ready_at"},
                {"target", "ready_at", "side"},
                {"target", "ready_at", "pending_intent"},
                {"target", "ready_at", "side", "pending_intent"},
            ):
                return False
            if not _positive_int(participant["target"]):
                return False
            if "side" in participant and participant["side"] not in {0, 1}:
                return False
            ready_at = participant["ready_at"]
            if isinstance(ready_at, bool) or not isinstance(ready_at, (int, float)):
                return False
            intent = participant.get("pending_intent")
            if intent is not None and not _valid_intent(intent):
                return False
    return all(int(encounter_id) < state["next_id"] for encounter_id in encounters)


def _repair_state(state: dict[str, Any]) -> None:
    """Prune invalid participants, targets, and one-sided encounters in place."""
    seen: set[int] = set()
    for encounter_id in sorted(tuple(state["encounters"]), key=int):
        encounter = state["encounters"].get(encounter_id)
        if encounter is None:
            continue
        participants = encounter["participants"]
        valid_ids = [
            int(actor_id)
            for actor_id in participants
            if int(actor_id) not in seen
            and _participant_is_valid(_get_character(int(actor_id)), encounter)
        ]
        for actor_id in list(participants):
            if int(actor_id) not in valid_ids:
                participants.pop(actor_id, None)
        if len(participants) < 2:
            _clear_encounter_conditions(encounter)
            del state["encounters"][encounter_id]
            _close_reward_ledger(int(encounter_id))
            continue
        sides = _participant_sides(encounter)
        for actor_id, side in sides.items():
            participants[str(actor_id)]["side"] = side
        if len(set(sides.values())) < 2:
            _clear_encounter_conditions(encounter)
            del state["encounters"][encounter_id]
            _close_reward_ledger(int(encounter_id))
            continue
        seen.update(valid_ids)
        for actor_id, record in participants.items():
            target_id = record["target"]
            if not _can_target(encounter, int(actor_id), target_id):
                record["target"] = _fallback_target(participants, int(actor_id))
        # A room object can disappear between checks; this expression ensures
        # all retained characters share the serialized room identity.
        if any(
            not _participant_is_valid(_get_character(int(actor_id)), encounter)
            for actor_id in participants
        ):
            _clear_encounter_conditions(encounter)
            del state["encounters"][encounter_id]
            _close_reward_ledger(int(encounter_id))


def _participant_is_valid(actor: Any, encounter: Mapping[str, Any]) -> bool:
    """Return whether a live character still belongs to encounter's room."""
    return (
        actor is not None
        and actor.location is not None
        and actor.location.id == encounter["room"]
    )


def _valid_encounter_target(
    actor: Any, target: Any, encounter: Mapping[str, Any]
) -> bool:
    """Require live distinct members sharing the encounter room."""
    return (
        actor.id != target.id
        and _participant_is_valid(actor, encounter)
        and _participant_is_valid(target, encounter)
        and str(target.id) in encounter["participants"]
        and _can_target(encounter, actor.id, target.id)
    )


def _valid_pair_reason(actor: Any, target: Any) -> str:
    """Return a safe validation reason for a requested combat pair."""
    if _object_id(actor) is None or _object_id(target) is None:
        return "participants must be characters"
    if actor.id == target.id:
        return "you cannot fight yourself"
    if (
        actor.location is None
        or target.location is None
        or actor.location != target.location
    ):
        return "participants must be in the same room"
    return ""


def _get_character(object_id: int) -> Any | None:
    """Resolve a dbref only when it remains a project Character instance."""
    try:
        from typeclasses.characters import Character

        return Character.objects.get(id=object_id)
    except Exception:
        return None


def _object_id(actor: Any) -> int | None:
    """Return actor's valid character dbref without trusting caller input."""
    object_id = getattr(actor, "id", None)
    if not _positive_int(object_id):
        return None
    resolved = _get_character(object_id)
    return object_id if resolved is actor else None


def _participant_encounter(state: Mapping[str, Any], actor_id: int) -> int | None:
    """Return the one encounter containing actor, repairing uniqueness elsewhere."""
    for encounter_id, encounter in state["encounters"].items():
        if str(actor_id) in encounter["participants"]:
            return int(encounter_id)
    return None


def _create_encounter(state: dict[str, Any], room_id: int, *actor_ids: int) -> int:
    """Create a new encounter and initialize its participants immediately."""
    encounter_id = state["next_id"]
    state["next_id"] += 1
    state["encounters"][str(encounter_id)] = {"room": room_id, "participants": {}}
    for index, actor_id in enumerate(actor_ids):
        _add_participant(state, encounter_id, actor_id, 0 if index == 0 else 1)
    return encounter_id


def _add_participant(
    state: dict[str, Any], encounter_id: int, actor_id: int, side: int
) -> bool:
    """Insert a participant with a readiness clock based on last processed token."""
    participants = state["encounters"][str(encounter_id)]["participants"]
    if str(actor_id) in participants:
        return False
    if side not in {0, 1}:
        raise CombatError("Combat participants must join one of two encounter sides.")
    participants[str(actor_id)] = {
        "target": actor_id,
        "ready_at": state["last_pulse"] + 1,
        "side": side,
        "pending_intent": None,
    }
    return True


def _set_target(
    state: dict[str, Any], encounter_id: int, actor_id: int, target_id: int
) -> bool:
    """Set a target when both ids currently belong to the same encounter."""
    participants = state["encounters"][str(encounter_id)]["participants"]
    record = participants.get(str(actor_id))
    if record is None or not _can_target(
        state["encounters"][str(encounter_id)], actor_id, target_id
    ):
        return False
    if record["target"] == target_id:
        return False
    record["target"] = target_id
    return True


def _merge_encounters(
    state: dict[str, Any], first_id: int, second_id: int, actor_id: int, target_id: int
) -> int:
    """Merge same-room records deterministically into their lower identity."""
    first = state["encounters"][str(first_id)]
    second = state["encounters"][str(second_id)]
    if _participant_sides(first)[actor_id] == _participant_sides(second)[target_id]:
        for member_id, side in _participant_sides(second).items():
            second["participants"][str(member_id)]["side"] = 1 - side
    keep_id, remove_id = sorted((first_id, second_id))
    keep = state["encounters"][str(keep_id)]
    remove = state["encounters"][str(remove_id)]
    if keep["room"] != remove["room"]:
        raise CombatError("Cannot merge combat encounters in different rooms.")
    keep["participants"].update(remove["participants"])
    del state["encounters"][str(remove_id)]
    _close_reward_ledger(remove_id)
    return keep_id


def _close_reward_ledger(encounter_id: int) -> None:
    """Notify the optional reward service when an encounter cannot continue."""
    try:
        from systems.rewards import close_encounter_ledger

        close_encounter_ledger(encounter_id)
    except Exception:
        logger.log_trace(
            f"Could not close COMBAT-07 ledger for encounter {encounter_id}."
        )


def _remove_participant(
    state: dict[str, Any], encounter_id: int, actor_id: int
) -> None:
    """Remove actor and ensure all survivors have deterministic valid targets."""
    participants = state["encounters"][str(encounter_id)]["participants"]
    actor = _get_character(actor_id)
    if actor is not None:
        try:
            from systems.tactical_combat import clear_combat_conditions

            clear_combat_conditions(actor)
        except Exception:
            logger.log_trace(
                f"Could not clear combat conditions for object #{actor_id}."
            )
    participants.pop(str(actor_id), None)
    if len(participants) < 2:
        _clear_encounter_conditions({"participants": participants})
        return
    if len(set(_participant_sides({"participants": participants}).values())) < 2:
        _clear_encounter_conditions({"participants": participants})
        return
    for survivor_id, record in participants.items():
        if not _can_target(
            {"participants": participants}, int(survivor_id), record["target"]
        ):
            record["target"] = _fallback_target(participants, int(survivor_id))


def _fallback_target(participants: Mapping[str, Any], actor_id: int) -> int:
    """Choose the lowest dbref opponent as a stable repair target."""
    sides = _participant_sides({"participants": participants})
    return min(
        candidate
        for candidate, side in sides.items()
        if candidate != actor_id and side != sides[actor_id]
    )


def _participant_sides(encounter: Mapping[str, Any]) -> dict[int, int]:
    """Return stable binary sides, upgrading legacy target-only records safely."""
    participants = encounter["participants"]
    sides = {
        int(actor_id): record["side"]
        for actor_id, record in participants.items()
        if record.get("side") in {0, 1}
    }
    for actor_id in sorted(int(value) for value in participants):
        sides.setdefault(actor_id, 0)
        target_id = participants[str(actor_id)]["target"]
        if str(target_id) in participants and target_id != actor_id:
            sides.setdefault(target_id, 1 - sides[actor_id])
    # Re-run once so target chains that begin after their target get a stable side.
    for actor_id in sorted(int(value) for value in participants):
        target_id = participants[str(actor_id)]["target"]
        if str(target_id) in participants and target_id != actor_id:
            if actor_id not in sides:
                sides[actor_id] = 1 - sides[target_id]
            elif target_id not in sides:
                sides[target_id] = 1 - sides[actor_id]
    return sides


def _opposing_side(encounter: Mapping[str, Any], actor_id: int) -> int:
    """Return the one binary side which may oppose an existing participant."""
    return 1 - _participant_sides(encounter)[actor_id]


def _can_target(encounter: Mapping[str, Any], actor_id: int, target_id: int) -> bool:
    """Require distinct members assigned to opposing encounter sides."""
    participants = encounter["participants"]
    if (
        actor_id == target_id
        or str(actor_id) not in participants
        or str(target_id) not in participants
    ):
        return False
    sides = _participant_sides(encounter)
    return sides[actor_id] != sides[target_id]


def _clear_encounter_conditions(encounter: Mapping[str, Any]) -> None:
    """Remove effects whose action restriction only has meaning in combat."""
    for actor_id in encounter.get("participants", {}):
        actor = _get_character(int(actor_id))
        if actor is None:
            continue
        try:
            from systems.tactical_combat import clear_combat_conditions

            clear_combat_conditions(actor)
        except Exception:
            logger.log_trace(
                f"Could not clear combat conditions for object #{actor_id}."
            )


def _refresh_prompts(*actors: Any) -> None:
    """Best-effort COMBAT-09 prompt refresh after a public state transition."""
    try:
        from systems.combat_controls import refresh_combat_prompt

        for actor in actors:
            if actor is not None:
                refresh_combat_prompt(actor)
    except Exception:
        logger.log_trace("Could not refresh a COMBAT-09 combat prompt.")


def _positive_int(value: Any) -> bool:
    """Return whether value is a non-boolean positive integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    """Return whether value is a non-boolean non-negative integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int_string(value: Any) -> bool:
    """Return whether value is a canonical positive-integer mapping key."""
    return isinstance(value, str) and value.isdigit() and _positive_int(int(value))


def _valid_intent(intent: Any) -> bool:
    """Accept only serializable COMBAT one-action intents."""
    if not isinstance(intent, Mapping):
        return False
    if intent.get("kind") == "tactical":
        try:
            from systems.tactical_combat import valid_tactical_intent

            return valid_tactical_intent(intent)
        except Exception:
            return False
    return (
        set(intent) == {"kind", "exit"}
        and intent.get("kind") == "flee"
        and _positive_int(intent.get("exit"))
    ) or (
        set(intent) == {"kind", "target"}
        and intent.get("kind") == "stabilize"
        and _positive_int(intent.get("target"))
    )


def _on_character_lifecycle(event: CharacterLifecycleEvent) -> None:
    """End combat except during COMBAT-06's exposed link-dead interval."""
    if event.availability is CharacterAvailability.UNAVAILABLE:
        if event.cause is not UnavailabilityCause.DISCONNECT:
            handle_departure(event.character)
        if event.cause is UnavailabilityCause.OOC:
            from systems.injury import handle_ooc_departure

            handle_ooc_departure(event.character)


def _on_server_lifecycle(event: ServerLifecycleEvent) -> None:
    """Validate retained encounters during either hot or cold recovery."""
    if event.phase is ServerTransitionPhase.RECOVER:
        recover_combat_state()


def _register_lifecycle_consumer() -> None:
    """Replace this code-owned callback safely when modules reload."""
    consumer = LifecycleConsumer(
        COMBAT_LIFECYCLE_KEY,
        on_character=_on_character_lifecycle,
        on_server=_on_server_lifecycle,
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(COMBAT_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


_register_lifecycle_consumer()

"""MOB-01's durable, single-action mobile behavior service.

WORLD-01 owns time; this module owns the per-NPC decision state it dispatches.
Behavior definitions live in a code registry, while NPC Attributes contain only
versioned primitive data that can safely be copied from a prototype.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from evennia.utils import logger
from systems.combat import is_fighting
from systems.combat_outcomes import InjuryState
from systems.injury import InjuryError, injury_record
from systems.mobile_navigation import register_mobile_behaviors
from systems.pulses import PulseEvent, PulseLane

MOBILE_BEHAVIOR_ATTRIBUTE = "mobile_behavior"
MOBILE_BEHAVIOR_PROFILE_ATTRIBUTE = "mobile_behavior_profile"
MOBILE_BEHAVIOR_VERSION = 1
IDLE_BEHAVIOR_KEY = "idle"
IDLE_ACTION_KEY = "idle"


class MobileBehaviorError(ValueError):
    """A mobile behavior definition or persisted record is invalid."""


@dataclass(frozen=True)
class MobileAction:
    """One selected action and the number of mobile tokens until the next one."""

    key: str
    data: Mapping[str, Any]
    delay_tokens: int = 1


@dataclass(frozen=True)
class MobileBehaviorDefinition:
    """Code-owned selection policy for one stable behavior key."""

    key: str
    select: Callable[[Any, PulseEvent, Mapping[str, Any]], MobileAction]


@dataclass(frozen=True)
class MobileActionDefinition:
    """Code-owned executor for one stable mobile action key."""

    key: str
    execute: Callable[[Any, PulseEvent, Mapping[str, Any]], None]


@dataclass(frozen=True)
class MobileOutcome:
    """One NPC result, using stable status and reason codes."""

    npc_id: int | None
    status: str
    reason: str
    behavior_key: str | None = None
    action_key: str | None = None


@dataclass(frozen=True)
class MobilePulseResult:
    """All independently isolated results from one mobile pulse."""

    outcomes: tuple[MobileOutcome, ...]

    @property
    def acted(self) -> int:
        """Return the number of non-idle actions that completed."""
        return sum(outcome.status == "acted" for outcome in self.outcomes)


_BEHAVIORS: dict[str, MobileBehaviorDefinition] = {}
_ACTIONS: dict[str, MobileActionDefinition] = {}


def behavior_profile_keys() -> tuple[str, ...]:
    """Return registered profile keys for builder validation and diagnostics."""
    return tuple(sorted(_BEHAVIORS))


def register_behavior(definition: MobileBehaviorDefinition) -> None:
    """Register one uniquely named, code-owned behavior selection policy."""
    _validate_registry_definition(definition.key, definition.select, "behavior")
    if definition.key in _BEHAVIORS:
        raise MobileBehaviorError(f"Behavior '{definition.key}' is already registered.")
    _BEHAVIORS[definition.key] = definition


def register_action(definition: MobileActionDefinition) -> None:
    """Register one uniquely named, code-owned mobile action executor."""
    _validate_registry_definition(definition.key, definition.execute, "action")
    if definition.key in _ACTIONS:
        raise MobileBehaviorError(f"Action '{definition.key}' is already registered.")
    _ACTIONS[definition.key] = definition


def initial_mobile_state(profile: Any = IDLE_BEHAVIOR_KEY) -> dict[str, Any]:
    """Build a validated, serializable state snapshot from prototype data."""
    behavior_key, config = _validate_profile(profile)
    return {
        "version": MOBILE_BEHAVIOR_VERSION,
        "behavior_key": behavior_key,
        "config": config,
        "next_eligible_token": 1,
        "last_consumed_token": 0,
        "paused": False,
        "pause_reason": None,
        "action_data": {},
    }


def mobile_state(npc: Any) -> dict[str, Any]:
    """Read detached state, initializing an NPC from its primitive profile."""
    raw = npc.attributes.get(MOBILE_BEHAVIOR_ATTRIBUTE)
    if raw is None:
        state = initial_mobile_state(
            npc.attributes.get(MOBILE_BEHAVIOR_PROFILE_ATTRIBUTE, IDLE_BEHAVIOR_KEY)
        )
        _write_state(npc, state)
        return state
    return _validate_state(raw)


def set_mobile_profile(npc: Any, profile: Any) -> dict[str, Any]:
    """Replace an NPC's profile and reset its safely idle runtime state."""
    state = initial_mobile_state(profile)
    _write_state(npc, state)
    return state


def pause_mobile(npc: Any, reason: str) -> dict[str, Any]:
    """Persistently pause a mobile without consuming future tokens."""
    if not isinstance(reason, str) or not reason.strip():
        raise MobileBehaviorError("A pause reason must be non-empty text.")
    state = mobile_state(npc)
    state["paused"] = True
    state["pause_reason"] = reason.strip()
    _write_state(npc, state)
    return state


def resume_mobile(npc: Any, *, next_token: int | None = None) -> dict[str, Any]:
    """Resume without replaying missed tokens, scheduling only future work."""
    state = mobile_state(npc)
    minimum = state["last_consumed_token"] + 1
    if next_token is None:
        next_token = minimum
    if (
        isinstance(next_token, bool)
        or not isinstance(next_token, int)
        or next_token < minimum
    ):
        raise MobileBehaviorError("The resumed mobile token must be in the future.")
    state["paused"] = False
    state["pause_reason"] = None
    state["next_eligible_token"] = next_token
    _write_state(npc, state)
    return state


def process_mobile_pulse(
    event: PulseEvent,
    *,
    mobiles: Iterable[Any] | None = None,
    select_action: (
        Callable[[Any, PulseEvent, Mapping[str, Any]], MobileAction] | None
    ) = None,
) -> MobilePulseResult:
    """Give each due, eligible NPC at most one isolated mobile decision."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.MOBILES:
        raise MobileBehaviorError(
            "Mobile behavior requires a mobiles-lane pulse event."
        )
    if mobiles is None:
        from typeclasses.characters import Character

        mobiles = (
            Character.objects.filter_family(db_location__isnull=False)
            .distinct()
            .iterator()
        )
    outcomes: list[MobileOutcome] = []
    for npc in mobiles:
        if getattr(npc.db, "is_player_character", None) is not False:
            continue
        try:
            outcome = _process_one(npc, event, select_action)
        except Exception:
            logger.log_trace(
                f"Mobile token {event.sequence} failed for object #{getattr(npc, 'id', '?')} "
                f"during heartbeat {event.heartbeat}."
            )
            outcome = MobileOutcome(getattr(npc, "id", None), "failed", "exception")
        outcomes.append(outcome)
    return MobilePulseResult(tuple(outcomes))


def _process_one(npc: Any, event: PulseEvent, injected_selector: Any) -> MobileOutcome:
    """Process one NPC while preserving token-before-extension idempotency."""
    npc_id = getattr(npc, "id", None)
    eligibility = _eligibility(npc)
    if eligibility is not None:
        return MobileOutcome(npc_id, "skipped", eligibility)
    try:
        state = mobile_state(npc)
    except MobileBehaviorError:
        return MobileOutcome(npc_id, "skipped", "malformed_state")
    if state["paused"]:
        return MobileOutcome(npc_id, "paused", "paused", state["behavior_key"])
    if event.sequence <= state["last_consumed_token"]:
        return MobileOutcome(
            npc_id, "skipped", "duplicate_token", state["behavior_key"]
        )
    if event.sequence < state["next_eligible_token"]:
        return MobileOutcome(npc_id, "skipped", "not_due", state["behavior_key"])

    behavior_key = state["behavior_key"]
    state["last_consumed_token"] = event.sequence
    state["next_eligible_token"] = event.sequence + 1
    _write_state(npc, state)  # Never replay work after selector/executor failure.
    # MOB-06 specials share this already-consumed MOB-01 decision token.  They
    # therefore cannot replay after a hot reload or manufacture a second action.
    from systems.mobile_specials import SpecialEvent, dispatch_specials

    specials = dispatch_specials(npc, SpecialEvent("decision", token=event.sequence))
    if specials.acted:
        behavior_key = next(
            key for key, outcome in specials.outcomes if outcome.status == "acted"
        )
        return MobileOutcome(npc_id, "acted", "special", behavior_key, "special")
    # MOB-03 owns the optional non-combat action layer.  It runs before the
    # behavior registry and consumes this token even when no action is eligible,
    # so a duplicate pulse cannot start a second encounter or take two items.
    from systems.mobile_policy import perform_autonomous_decision

    policy = perform_autonomous_decision(npc)
    if policy.allowed:
        return MobileOutcome(
            npc_id, "acted", policy.reason, behavior_key, policy.reason
        )
    if policy.reason == "malformed_mobile_policy":
        return MobileOutcome(npc_id, "skipped", policy.reason, behavior_key)
    # MOB-07 consumes this already-owned mobile decision for a follow step;
    # it cannot teleport or manufacture an additional autonomous action.
    from systems.mobile_relationships import advance_follow

    following = advance_follow(npc, event.sequence)
    if following is not None and following.reason != "arrived":
        return MobileOutcome(
            npc_id,
            "acted" if following.status == "acted" else "skipped",
            following.reason,
            behavior_key,
            "follow",
        )
    # A pursuit intent has priority over ordinary behavior, but still consumes
    # this one MOB-01 token and can traverse no more than one exit.
    from systems.mobile_navigation import pursue

    pursuit = pursue(npc, event.sequence)
    if pursuit is not None:
        status = "acted" if pursuit.status == "moved" else "skipped"
        return MobileOutcome(npc_id, status, pursuit.reason, behavior_key, "pursuit")
    behavior = _BEHAVIORS.get(behavior_key)
    if behavior is None:
        return MobileOutcome(npc_id, "failed", "unknown_behavior", behavior_key)
    try:
        action = _validate_action(
            (injected_selector or behavior.select)(
                npc, event, deepcopy(state["config"])
            )
        )
    except Exception:
        logger.log_trace(
            f"Mobile token {event.sequence} selection failed for object #{npc_id} with behavior '{behavior_key}'."
        )
        return MobileOutcome(npc_id, "failed", "selection_failed", behavior_key)

    state["action_data"] = _primitive_copy(action.data, "action data")
    state["next_eligible_token"] = event.sequence + action.delay_tokens
    _write_state(npc, state)
    executor = _ACTIONS.get(action.key)
    if executor is None:
        return MobileOutcome(
            npc_id, "failed", "unknown_action", behavior_key, action.key
        )
    try:
        executor.execute(npc, event, deepcopy(state["action_data"]))
    except Exception:
        logger.log_trace(
            f"Mobile token {event.sequence} action '{action.key}' failed for object #{npc_id} with behavior '{behavior_key}'."
        )
        return MobileOutcome(
            npc_id, "failed", "action_failed", behavior_key, action.key
        )
    status = "idle" if action.key == IDLE_ACTION_KEY else "acted"
    return MobileOutcome(
        npc_id,
        status,
        "idle" if status == "idle" else "acted",
        behavior_key,
        action.key,
    )


def _eligibility(npc: Any) -> str | None:
    """Return a fail-closed reason when an NPC must not take a mobile action."""
    if getattr(npc, "location", None) is None:
        return "off_grid"
    try:
        if injury_record(npc).state is not InjuryState.CONSCIOUS:
            return "incapacitated"
    except (InjuryError, AttributeError, TypeError):
        return "invalid_injury"
    return "fighting" if is_fighting(npc) else None


def _validate_profile(profile: Any) -> tuple[str, dict[str, Any]]:
    """Validate a prototype profile without admitting code or live objects."""
    if isinstance(profile, str):
        key, config = profile, {}
    elif isinstance(profile, Mapping) and set(profile) == {"key", "config"}:
        key, config = profile["key"], profile["config"]
    else:
        raise MobileBehaviorError("Mobile behavior profile is invalid.")
    if not isinstance(key, str) or not key or key not in _BEHAVIORS:
        raise MobileBehaviorError("Mobile behavior profile is unknown.")
    return key, _primitive_copy(config, "behavior configuration")


def _validate_state(raw: Any) -> dict[str, Any]:
    """Validate and detach persisted mobile state."""
    expected = {
        "version",
        "behavior_key",
        "config",
        "next_eligible_token",
        "last_consumed_token",
        "paused",
        "pause_reason",
        "action_data",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != expected
        or raw.get("version") != MOBILE_BEHAVIOR_VERSION
    ):
        raise MobileBehaviorError("Mobile behavior state is invalid.")
    key, next_token, last_token = (
        raw["behavior_key"],
        raw["next_eligible_token"],
        raw["last_consumed_token"],
    )
    if (
        not isinstance(key, str)
        or not key
        or isinstance(next_token, bool)
        or not isinstance(next_token, int)
        or next_token < 1
        or isinstance(last_token, bool)
        or not isinstance(last_token, int)
        or last_token < 0
        or next_token <= last_token
        or not isinstance(raw["paused"], bool)
        or (
            raw["pause_reason"] is not None
            and (not isinstance(raw["pause_reason"], str) or not raw["pause_reason"])
        )
    ):
        raise MobileBehaviorError("Mobile behavior state has invalid values.")
    return {
        "version": MOBILE_BEHAVIOR_VERSION,
        "behavior_key": key,
        "config": _primitive_copy(raw["config"], "behavior configuration"),
        "next_eligible_token": next_token,
        "last_consumed_token": last_token,
        "paused": raw["paused"],
        "pause_reason": raw["pause_reason"],
        "action_data": _primitive_copy(raw["action_data"], "action data"),
    }


def _primitive_copy(value: Any, label: str) -> Any:
    """Detach JSON-like data and reject callables, objects, and non-string keys."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_primitive_copy(item, label) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) and key for key in value):
            raise MobileBehaviorError(f"Mobile {label} keys must be non-empty strings.")
        return {key: _primitive_copy(item, label) for key, item in value.items()}
    raise MobileBehaviorError(f"Mobile {label} must contain primitive data only.")


def _validate_action(action: Any) -> MobileAction:
    """Validate one selection result before it becomes durable action data."""
    if (
        not isinstance(action, MobileAction)
        or not isinstance(action.key, str)
        or not action.key
        or isinstance(action.delay_tokens, bool)
        or not isinstance(action.delay_tokens, int)
        or action.delay_tokens < 1
    ):
        raise MobileBehaviorError("Mobile action selection is invalid.")
    return MobileAction(
        action.key, _primitive_copy(action.data, "action data"), action.delay_tokens
    )


def _validate_registry_definition(key: Any, callback: Any, kind: str) -> None:
    """Keep extension identities stable and callbacks code-owned."""
    if not isinstance(key, str) or not key or not callable(callback):
        raise MobileBehaviorError(
            f"Mobile {kind} definitions require a key and callback."
        )


def _write_state(npc: Any, state: Mapping[str, Any]) -> None:
    """Persist only a freshly validated primitive snapshot."""
    npc.attributes.add(MOBILE_BEHAVIOR_ATTRIBUTE, _validate_state(state))


def _select_idle(
    npc: Any, event: PulseEvent, config: Mapping[str, Any]
) -> MobileAction:
    """Select the safe default profile's only action."""
    return MobileAction(IDLE_ACTION_KEY, {})


def _execute_idle(npc: Any, event: PulseEvent, data: Mapping[str, Any]) -> None:
    """Deliberately do nothing until a later MOB item supplies policy."""


register_action(MobileActionDefinition(IDLE_ACTION_KEY, _execute_idle))
register_behavior(MobileBehaviorDefinition(IDLE_BEHAVIOR_KEY, _select_idle))

# The navigation module registers code-owned behavior keys only after the base
# registry exists; no callbacks are ever persisted on an NPC.
register_mobile_behaviors()

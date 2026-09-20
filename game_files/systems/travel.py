"""INTERACT-03 sector rules, travel calculation, and queued PC traversal."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from evennia.objects.models import ObjectDB
from systems.action_policy import ActionCategory
from systems.action_queue import (
    ActionDefinition,
    ActionQueueError,
    ActionResult,
    ActionStatus,
    current_action_sequence,
    inspect_action,
    register_action_definition,
    schedule_action,
    unregister_action_definition,
)
from systems.doors import traversal_decision
from systems.effects import EffectStorageError
from systems.encumbrance import character_load
from systems.room_policy import AdmissionMode, admission_decision

SECTOR_ATTRIBUTE = "sector"
TRAVEL_ACTION_KEY = "interact03.travel"
TRAVEL_CONFLICT_GROUP = "movement"
MIN_WEATHER_MULTIPLIER = 0.25
MAX_WEATHER_MULTIPLIER = 4.0


class TravelRequirement(str, Enum):
    """Special capability needed to enter a sector."""

    NONE = "none"
    WATER = "boat_or_swim"
    FLIGHT = "flight"


@dataclass(frozen=True)
class Sector:
    """One immutable, code-owned terrain definition."""

    key: str
    cost: int
    requirement: TravelRequirement = TravelRequirement.NONE


@dataclass(frozen=True)
class TravelDecision:
    """Side-effect-free route legality and action-token cost."""

    allowed: bool
    reason: str = ""
    delay: int | None = None


@dataclass(frozen=True)
class TravelCompletion:
    """One post-commit voluntary PC exit traversal, expressed as primitives."""

    actor_id: int
    source_id: int
    destination_id: int
    exit_id: int
    action_id: str
    action_token: int


SECTORS = MappingProxyType(
    {
        sector.key: sector
        for sector in (
            Sector("inside", 1),
            Sector("city", 1),
            Sector("field", 2),
            Sector("forest", 3),
            Sector("hills", 4),
            Sector("mountain", 6),
            Sector("shallow_water", 4),
            Sector("deep_water", 6, TravelRequirement.WATER),
            Sector("air", 6, TravelRequirement.FLIGHT),
        )
    }
)


def sector_key(room: Any) -> str | None:
    """Return a valid authored sector, defaulting absent data to ``inside``."""
    raw = room.attributes.get(SECTOR_ATTRIBUTE, default=None)
    if raw is None:
        return "inside"
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return key if key in SECTORS else None


def weather_travel_multiplier(destination: Any, actor: Any) -> float:
    """Adapt ENV-02 when installed; otherwise weather is neutral."""
    try:
        from systems.weather import travel_multiplier
    except ImportError:
        return 1.0
    return travel_multiplier(destination, actor)


def _has_condition(actor: Any, condition: str) -> bool:
    try:
        return bool(actor.effects.has_condition(condition))
    except (AttributeError, EffectStorageError):
        return False


def _has_functional_boat(actor: Any) -> bool:
    """Accept a directly carried, intact ITEM-08B boat capability."""
    from systems.equipment_capabilities import item_equipment_capabilities

    return any(
        "terrain:boat" in item_equipment_capabilities(item)
        and item.attributes.get("broken", default=False) is False
        for item in getattr(actor, "contents", ())
    )


def _meets_requirement(actor: Any, requirement: TravelRequirement) -> bool:
    if requirement is TravelRequirement.NONE:
        return True
    if requirement is TravelRequirement.WATER:
        from systems.equipment_capabilities import has_equipment_capability

        return (
            _has_functional_boat(actor)
            or has_equipment_capability(actor, "terrain:swim")
            or _has_condition(actor, "swim")
        )
    from systems.equipment_capabilities import has_equipment_capability

    return has_equipment_capability(actor, "terrain:flight") or _has_condition(
        actor, "flight"
    )


def travel_decision(actor: Any, exit_obj: Any) -> TravelDecision:
    """Calculate one route's legality and rounded-up action-token delay."""
    source = getattr(actor, "location", None)
    destination = getattr(exit_obj, "destination", None)
    if source is None or getattr(exit_obj, "location", None) is not source:
        return TravelDecision(False, "source_changed")
    if destination is None or destination is source:
        return TravelDecision(False, "invalid_destination")
    if not actor.actions.check(ActionCategory.MOVE).allowed:
        return TravelDecision(False, "movement_denied")
    if character_load(actor).overloaded:
        return TravelDecision(False, "overloaded")
    door = traversal_decision(exit_obj)
    if not door.allowed or not exit_obj.access(actor, "traverse", default=False):
        return TravelDecision(False, "exit_blocked")
    if not admission_decision(actor, destination, mode=AdmissionMode.NORMAL).allowed:
        return TravelDecision(False, "admission_denied")
    key = sector_key(destination)
    if key is None:
        return TravelDecision(False, "invalid_sector")
    sector = SECTORS[key]
    if (
        not isinstance(sector.cost, int)
        or isinstance(sector.cost, bool)
        or sector.cost < 1
    ):
        return TravelDecision(False, "invalid_cost")
    if not _meets_requirement(actor, sector.requirement):
        return TravelDecision(False, sector.requirement.value)
    try:
        multiplier = float(weather_travel_multiplier(destination, actor))
    except (TypeError, ValueError):
        return TravelDecision(False, "invalid_weather")
    if (
        not math.isfinite(multiplier)
        or not MIN_WEATHER_MULTIPLIER <= multiplier <= MAX_WEATHER_MULTIPLIER
    ):
        return TravelDecision(False, "invalid_weather")
    delay = actor.stats.movement_delay(sector.cost * multiplier)
    if delay is None:
        return TravelDecision(False, "immobilized")
    return TravelDecision(True, delay=max(1, math.ceil(delay)))


def schedule_travel(
    actor: Any, exit_obj: Any, *, follow_event: TravelCompletion | None = None
) -> ActionResult:
    """Reserve a normal PC exit traversal without moving immediately."""
    decision = travel_decision(actor, exit_obj)
    if not decision.allowed or decision.delay is None:
        return ActionResult(ActionStatus.DECLINED, reason=decision.reason)
    arguments: dict[str, Any] = {
        "source_id": actor.location.id,
        "exit_id": exit_obj.id,
        "destination_id": exit_obj.destination.id,
    }
    if follow_event is not None:
        arguments.update(
            follow_leader_id=follow_event.actor_id,
            follow_event_id=follow_event.action_id,
            follow_event_token=follow_event.action_token,
        )
    return schedule_action(
        actor,
        TRAVEL_ACTION_KEY,
        arguments,
        delay=decision.delay,
    )


def _resolve(args: Mapping[str, Any]) -> tuple[Any, Any] | None:
    try:
        exit_obj = ObjectDB.objects.get(id=args["exit_id"])
        destination = ObjectDB.objects.get(id=args["destination_id"])
    except (KeyError, ObjectDB.DoesNotExist, TypeError, ValueError):
        return None
    return exit_obj, destination


def _validate(actor: Any, args: Mapping[str, Any]) -> bool | str:
    def result(reason: str) -> str:
        """Notify only during due-action revalidation, never initial scheduling."""
        if args.get("follow_leader_id") is not None:
            try:
                from systems.group_following import propagation_failed
                from systems.player_following import pc_by_id

                propagation_failed(actor, pc_by_id(args["follow_leader_id"]), reason)
            except Exception:
                pass
            return reason
        active = inspect_action(actor)
        if active is not None and active.get("definition") == TRAVEL_ACTION_KEY:
            actor.msg(denial_message(reason))
        return reason

    follow_reason = _follow_validation_reason(actor, args)
    if follow_reason:
        return result(follow_reason)
    resolved = _resolve(args)
    if resolved is None or getattr(actor.location, "id", None) != args.get("source_id"):
        return result("route_changed")
    exit_obj, destination = resolved
    if exit_obj.destination is not destination:
        return result("route_changed")
    decision = travel_decision(actor, exit_obj)
    return True if decision.allowed else result(decision.reason)


def _execute(actor: Any, args: Mapping[str, Any], reservation: Mapping[str, Any]):
    resolved = _resolve(args)
    if resolved is None:
        return "route_changed"
    exit_obj, destination = resolved
    exit_obj.at_traverse(actor, destination, travel_execution=True)
    if actor.location is not destination:
        return "traversal_denied"
    actor.msg(f"You arrive at {destination.key}.")
    active = inspect_action(actor) or {}
    action_token = active.get("due_token", current_action_sequence())
    _emit_completion(
        TravelCompletion(
            actor_id=actor.id,
            source_id=args["source_id"],
            destination_id=destination.id,
            exit_id=exit_obj.id,
            action_id=active.get("id", f"travel:{actor.id}:{action_token}"),
            action_token=action_token,
        )
    )
    return None


def _follow_validation_reason(actor: Any, args: Mapping[str, Any]) -> str | None:
    """Validate the extra edge constraints on a queued propagated traversal."""
    leader_id = args.get("follow_leader_id")
    if leader_id is None:
        return None
    try:
        from systems.player_following import follow_state, pc_by_id

        leader = pc_by_id(leader_id)
        if leader is None or follow_state(actor)["leader_id"] != leader_id:
            return "follow_link_changed"
        if actor.location is None or getattr(actor.location, "id", None) != args.get(
            "source_id"
        ):
            return "follow_source_changed"
        from systems.action_policy import Position
        from systems.combat import is_fighting
        from systems.combat_outcomes import InjuryState
        from systems.injury import injury_record
        from systems.visibility import room_visibility, target_visibility

        if injury_record(actor).state is not InjuryState.CONSCIOUS:
            return "follow_not_conscious"
        if actor.actions.position is not Position.STANDING:
            return "follow_not_standing"
        if is_fighting(actor):
            return "follow_in_combat"
        if not room_visibility(actor, actor.location).visible:
            return "follow_departure_hidden"
        resolved = _resolve(args)
        if resolved is None or not target_visibility(actor, resolved[0]).visible:
            return "follow_departure_hidden"
    except Exception:
        return "follow_invalid"
    return None


def _emit_completion(event: TravelCompletion) -> None:
    """Offer one committed voluntary traversal to GROUP-01B, if installed."""
    try:
        from systems.group_following import propagate_travel_completion

        propagate_travel_completion(event)
    except Exception:
        # Movement is already committed; a bad follower record must never undo
        # travel or block independent movement consumers.
        from evennia.utils import logger

        logger.log_trace("GROUP-01B propagation failed after committed travel.")


def denial_message(reason: str) -> str:
    """Map internal travel reasons to non-revealing player text."""
    if reason == TravelRequirement.WATER.value:
        return "You need a functional boat or the ability to swim to go there."
    if reason == TravelRequirement.FLIGHT.value:
        return "You need the ability to fly to go there."
    if reason == "overloaded":
        return "You are too encumbered to move. Drop or give away some items."
    if reason in {"immobilized", "movement_denied"}:
        return "You cannot move right now."
    return "You cannot travel that way right now."


def _register() -> None:
    definition = ActionDefinition(
        TRAVEL_ACTION_KEY,
        TRAVEL_CONFLICT_GROUP,
        _validate,
        _execute,
        survive_reload=True,
        disconnect_safe=False,
    )
    try:
        register_action_definition(definition)
    except ActionQueueError:
        unregister_action_definition(TRAVEL_ACTION_KEY)
        register_action_definition(definition)


_register()

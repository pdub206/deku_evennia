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
    """Accept only a directly carried, usable boat item."""
    for item in getattr(actor, "contents", ()):
        if str(item.attributes.get("type", default="")).strip().lower() != "boat":
            continue
        if item.attributes.get("broken", default=False) is not False:
            continue
        return True
    return False


def _meets_requirement(actor: Any, requirement: TravelRequirement) -> bool:
    if requirement is TravelRequirement.NONE:
        return True
    if requirement is TravelRequirement.WATER:
        return _has_functional_boat(actor) or _has_condition(actor, "swim")
    return _has_condition(actor, "flight")


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


def schedule_travel(actor: Any, exit_obj: Any) -> ActionResult:
    """Reserve a normal PC exit traversal without moving immediately."""
    decision = travel_decision(actor, exit_obj)
    if not decision.allowed or decision.delay is None:
        return ActionResult(ActionStatus.DECLINED, reason=decision.reason)
    return schedule_action(
        actor,
        TRAVEL_ACTION_KEY,
        {
            "source_id": actor.location.id,
            "exit_id": exit_obj.id,
            "destination_id": exit_obj.destination.id,
        },
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
        active = inspect_action(actor)
        if active is not None and active.get("definition") == TRAVEL_ACTION_KEY:
            actor.msg(denial_message(reason))
        return reason

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
    return None


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

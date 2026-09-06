"""MOB-04's safe, one-step navigation service for non-player characters.

Route plans are deliberately ephemeral.  The only durable navigation data is a
small pursuit intent containing database ids and a remaining step budget; exits
and rooms are always resolved and revalidated immediately before traversal.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from evennia.utils import logger
from systems.action_policy import ActionCategory
from systems.encumbrance import character_load
from systems.injury import InjuryError, InjuryState, injury_record

MOBILE_NAVIGATION_ATTRIBUTE = "mobile_navigation"
MOBILE_NAVIGATION_VERSION = 1
PURSUIT_STEP_LIMIT = 8
WANDER_BEHAVIOR_KEY = "wander"
WANDER_ACTION_KEY = "mobile_navigation.wander"


@dataclass(frozen=True)
class NavigationRequest:
    """One non-persistent request to select and traverse a single exit."""

    purpose: str
    actor: Any
    token: int
    target: Any | None = None
    destination: Any | None = None
    stay_in_area: bool | None = None


@dataclass(frozen=True)
class NavigationOutcome:
    """A player-safe result from one navigation request."""

    status: str
    reason: str = ""
    exit_id: int | None = None


ExitSelector = Callable[[tuple[Any, ...]], Any]


def room_admission(actor: Any, destination: Any) -> NavigationOutcome:
    """Apply the single fail-closed NPC room-admission seam.

    ``no_mobiles``, ``forbid_mobiles``, ``private``, and ``mobile_capacity``
    are intentionally compact extension attributes until ENV-01 owns a richer
    room-flag catalogue.  Invalid values never grant admission.
    """
    from typeclasses.rooms import Room

    if not isinstance(destination, Room) or getattr(destination, "id", None) is None:
        return NavigationOutcome("blocked", "invalid_destination")
    values = {
        name: destination.attributes.get(name)
        for name in ("no_mobiles", "forbid_mobiles", "private", "mobile_capacity")
    }
    for name in ("no_mobiles", "forbid_mobiles", "private"):
        if values[name] is not None and not isinstance(values[name], bool):
            return NavigationOutcome("blocked", "malformed_admission")
        if values[name] is True:
            return NavigationOutcome("blocked", "room_forbidden")
    capacity = values["mobile_capacity"]
    if capacity is not None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
            return NavigationOutcome("blocked", "malformed_admission")
        occupants = sum(
            1
            for obj in destination.contents_get(content_type="character")
            if obj is not actor
        )
        if occupants >= capacity:
            return NavigationOutcome("blocked", "room_full")
    return NavigationOutcome("moved")


def exit_eligibility(
    actor: Any,
    exit_obj: Any,
    *,
    purpose: str = "wander",
    stay_in_area: bool | None = None,
    source: Any | None = None,
    require_current_room: bool = True,
    allow_fighting: bool = False,
) -> NavigationOutcome:
    """Validate one route without moving or revealing route internals."""
    from systems.combat import is_fighting
    from typeclasses.characters import Character
    from typeclasses.exits import Exit
    from typeclasses.rooms import Room

    if (
        not isinstance(actor, Character)
        or getattr(actor.db, "is_player_character", None) is not False
    ):
        return NavigationOutcome("skipped", "not_mobile")
    source = actor.location if source is None else source
    if not isinstance(source, Room) or (
        require_current_room and actor.location is not source
    ):
        return NavigationOutcome("skipped", "off_grid")
    if not allow_fighting and is_fighting(actor):
        return NavigationOutcome("skipped", "fighting")
    try:
        if injury_record(actor).state is not InjuryState.CONSCIOUS:
            return NavigationOutcome("skipped", "incapacitated")
    except (InjuryError, AttributeError, TypeError):
        return NavigationOutcome("skipped", "invalid_injury")
    if not allow_fighting and not actor.actions.check(ActionCategory.MOVE).allowed:
        return NavigationOutcome("skipped", "movement_denied")
    if not allow_fighting and character_load(actor).overloaded:
        return NavigationOutcome("skipped", "overloaded")
    if not isinstance(exit_obj, Exit) or exit_obj.location is not source:
        return NavigationOutcome("no-route", "invalid_exit")
    destination = exit_obj.destination
    if (
        not isinstance(destination, Room)
        or destination is source
        or getattr(destination, "id", None) is None
    ):
        return NavigationOutcome("no-route", "invalid_destination")
    for name in ("closed", "hidden"):
        value = exit_obj.attributes.get(name)
        if value is not None and not isinstance(value, bool):
            return NavigationOutcome("no-route", "malformed_exit")
        if value:
            return NavigationOutcome("blocked", "exit_blocked")
    if not exit_obj.access(actor, "traverse", default=False):
        return NavigationOutcome("blocked", "exit_blocked")
    constrained = _resolve_area_constraint(actor, stay_in_area)
    if isinstance(constrained, NavigationOutcome):
        return constrained
    if constrained and not _same_authored_area(source, destination):
        return NavigationOutcome("no-route", "area_constrained")
    admission = room_admission(actor, destination)
    if admission.status != "moved":
        return admission
    return NavigationOutcome("moved", exit_id=exit_obj.id)


def eligible_exits(
    actor: Any,
    *,
    purpose: str = "wander",
    stay_in_area: bool | None = None,
    source: Any | None = None,
    require_current_room: bool = True,
    allow_fighting: bool = False,
) -> tuple[Any, ...]:
    """Return side-effect-free eligible exits in stable order."""
    room = actor.location if source is None else source
    exits = getattr(room, "exits", ())
    valid = [
        exit_obj
        for exit_obj in exits
        if exit_eligibility(
            actor,
            exit_obj,
            purpose=purpose,
            stay_in_area=stay_in_area,
            source=room,
            require_current_room=require_current_room,
            allow_fighting=allow_fighting,
        ).status
        == "moved"
    ]
    return tuple(sorted(valid, key=lambda item: (item.key.casefold(), item.id)))


def execute_navigation(request: NavigationRequest, exit_obj: Any) -> NavigationOutcome:
    """Revalidate and traverse exactly one selected exit through normal hooks."""
    if (
        not isinstance(request.token, int)
        or isinstance(request.token, bool)
        or request.token < 1
    ):
        return NavigationOutcome("failed", "invalid_token")
    if request.purpose not in {"wander", "pursuit", "follow", "flee", "special"}:
        return NavigationOutcome("failed", "invalid_purpose")
    allow_fighting = request.purpose == "flee"
    state = navigation_state(request.actor)
    if request.token <= state["last_successful_token"]:
        return NavigationOutcome("skipped", "duplicate_token")
    decision = exit_eligibility(
        request.actor,
        exit_obj,
        purpose=request.purpose,
        stay_in_area=request.stay_in_area,
        allow_fighting=allow_fighting,
    )
    if decision.status != "moved":
        return decision
    source, destination = request.actor.location, exit_obj.destination
    try:
        # Exit traversal, rather than a direct room move, preserves ordinary
        # departure, arrival, announcements, and future terrain hooks.
        exit_obj.at_traverse(request.actor, destination, mobile_navigation=True)
    except Exception:
        logger.log_trace(
            f"Mobile navigation failed for #{getattr(request.actor, 'id', '?')} token {request.token} purpose {request.purpose}."
        )
        from systems.mobile_diagnostics import record_mobile_failure

        record_mobile_failure(
            request.actor,
            "navigation",
            "traversal_failed",
            token=request.token,
            purpose=request.purpose,
        )
        return NavigationOutcome("failed", "traversal_failed")
    if request.actor.location is source or request.actor.location is not destination:
        return NavigationOutcome("blocked", "traversal_denied")
    state["last_successful_token"] = request.token
    _write_state(request.actor, state)
    return NavigationOutcome("moved", exit_id=exit_obj.id)


def wander(
    actor: Any, token: int, *, selector: ExitSelector | None = None
) -> NavigationOutcome:
    """Select and traverse one ordinary legal exit, if any."""
    from systems.mobile_policy import permits_ordinary_wandering

    policy = permits_ordinary_wandering(actor)
    if not policy.allowed:
        return NavigationOutcome("skipped", policy.reason)
    exits = eligible_exits(actor, purpose="wander")
    if not exits:
        return NavigationOutcome("no-route", "no_route")
    selected = selector(exits) if selector is not None else exits[0]
    if selected not in exits:
        return NavigationOutcome("failed", "invalid_selection")
    return execute_navigation(NavigationRequest("wander", actor, token), selected)


def begin_pursuit(
    actor: Any, target: Any, *, step_limit: int = PURSUIT_STEP_LIMIT
) -> bool:
    """Persist a bounded primitive pursuit intent after a target departs."""
    if (
        isinstance(step_limit, bool)
        or not isinstance(step_limit, int)
        or not 1 <= step_limit <= PURSUIT_STEP_LIMIT
        or not _live_id(target)
    ):
        return False
    state = navigation_state(actor)
    state["pursuit"] = {"target_id": target.id, "remaining_steps": step_limit}
    _write_state(actor, state)
    return True


def note_target_departure(target: Any, source: Any) -> None:
    """Give eligible NPC opponents one bounded intent when a target leaves.

    This deliberately observes the encounter before Character's normal move
    cleanup removes the target.  It creates no encounter and performs no move.
    """
    if source is None or getattr(target, "location", None) is source:
        return
    try:
        from systems.combat import combat_opponents

        opponents = combat_opponents(target)
    except Exception:
        return
    for opponent in opponents:
        if (
            getattr(getattr(opponent, "db", None), "is_player_character", None) is False
            and getattr(opponent, "location", None) is source
        ):
            begin_pursuit(opponent, target)


def pursue(actor: Any, token: int) -> NavigationOutcome | None:
    """Advance one bounded shortest-path pursuit step or stop it safely."""
    state = navigation_state(actor)
    intent = state["pursuit"]
    if intent is None:
        return None
    target = _resolve_character(intent["target_id"])
    if not _pursuit_target_allowed(actor, target):
        return _clear_pursuit(actor, state, "target_lost")
    if actor.location is target.location:
        return _arrive_pursuit(actor, target, state)
    if intent["remaining_steps"] <= 0:
        return _clear_pursuit(actor, state, "pursuit_bound")
    stay_in_area = _resolve_area_constraint(actor, None)
    if isinstance(stay_in_area, NavigationOutcome):
        return _clear_pursuit(actor, state, stay_in_area.reason)
    route = _shortest_first_exit(actor, target.location, stay_in_area)
    if route is None:
        return _clear_pursuit(actor, state, "no_route")
    outcome = execute_navigation(
        NavigationRequest(
            "pursuit", actor, token, target=target, stay_in_area=stay_in_area
        ),
        route,
    )
    if outcome.status != "moved":
        return outcome
    state = navigation_state(actor)
    intent = state["pursuit"]
    if intent is not None:
        intent["remaining_steps"] -= 1
        state["pursuit"] = intent
        _write_state(actor, state)
    if actor.location is target.location:
        _arrive_pursuit(actor, target, navigation_state(actor))
    return outcome


def follow_step(actor: Any, leader: Any, token: int) -> NavigationOutcome:
    """Traverse one legal shortest step toward a relationship leader.

    MOB-07 owns whether following is wanted and how long it may continue;
    MOB-04 continues to own route selection, locks, admission, and traversal.
    """
    if getattr(leader, "location", None) is None:
        return NavigationOutcome("skipped", "target_lost")
    stay_in_area = _resolve_area_constraint(actor, None)
    if isinstance(stay_in_area, NavigationOutcome):
        return stay_in_area
    route = _shortest_first_exit(actor, leader.location, stay_in_area)
    if route is None:
        return NavigationOutcome("no-route", "no_route")
    return execute_navigation(
        NavigationRequest(
            "follow", actor, token, target=leader, stay_in_area=stay_in_area
        ),
        route,
    )


def navigation_state(actor: Any) -> dict[str, Any]:
    """Read the detached, primitive-only durable navigation state."""
    raw = actor.attributes.get(MOBILE_NAVIGATION_ATTRIBUTE)
    if raw is None:
        return {
            "version": MOBILE_NAVIGATION_VERSION,
            "last_successful_token": 0,
            "pursuit": None,
        }
    return _validate_state(raw)


def _select_wander(npc: Any, event: Any, config: Mapping[str, Any]) -> Any:
    """Select the registered one-step wandering action."""
    from systems.mobiles import MobileAction

    return MobileAction(WANDER_ACTION_KEY, {"token": event.sequence})


def _execute_wander(npc: Any, event: Any, data: Mapping[str, Any]) -> None:
    """Execute the selected navigation action without serializing objects."""
    token = data.get("token")
    outcome = wander(npc, token)
    npc.ndb.mobile_navigation_outcome = outcome


def register_mobile_behaviors() -> None:
    """Register code-owned MOB-04 behavior keys exactly once per reload."""
    from systems.mobiles import (MobileActionDefinition,
                                 MobileBehaviorDefinition, register_action,
                                 register_behavior)

    try:
        register_action(MobileActionDefinition(WANDER_ACTION_KEY, _execute_wander))
    except ValueError:
        pass
    try:
        register_behavior(MobileBehaviorDefinition(WANDER_BEHAVIOR_KEY, _select_wander))
    except ValueError:
        pass


def _resolve_area_constraint(
    actor: Any, requested: bool | None
) -> bool | NavigationOutcome:
    if requested is not None:
        if not isinstance(requested, bool):
            return NavigationOutcome("failed", "malformed_constraint")
        return requested
    from systems.mobile_policy import navigation_area_constraint

    policy = navigation_area_constraint(actor)
    if not policy.allowed and policy.reason == "malformed_mobile_policy":
        return NavigationOutcome("skipped", policy.reason)
    return policy.allowed


def _same_authored_area(source: Any, destination: Any) -> bool:
    return _area_identity(source) is not None and _area_identity(
        source
    ) == _area_identity(destination)


def _area_identity(room: Any) -> str | None:
    from systems.areas import AREA_TAG_CATEGORY

    try:
        values = room.tags.get(category=AREA_TAG_CATEGORY, return_list=True)
    except (AttributeError, TypeError):
        return None
    return (
        values[0]
        if len(values) == 1 and isinstance(values[0], str) and values[0]
        else None
    )


def _shortest_first_exit(
    actor: Any, destination: Any, stay_in_area: bool
) -> Any | None:
    """Find the deterministic first step of a legal breadth-first route."""
    source = actor.location
    if source is None or destination is None:
        return None
    queue = deque([(source, None)])
    visited = {source.id}
    while queue:
        room, first = queue.popleft()
        for exit_obj in eligible_exits(
            actor,
            purpose="pursuit",
            stay_in_area=stay_in_area,
            source=room,
            require_current_room=False,
        ):
            next_room = exit_obj.destination
            if next_room.id in visited:
                continue
            selected = exit_obj if first is None else first
            if next_room is destination:
                return selected
            visited.add(next_room.id)
            queue.append((next_room, selected))
    return None


def _pursuit_target_allowed(actor: Any, target: Any) -> bool:
    from systems.mobile_policy import (can_detect_remotely, is_protected,
                                       may_enter_combat)

    if target is None or target.location is None or is_protected(target):
        return False
    try:
        return (
            injury_record(target).state is InjuryState.CONSCIOUS
            and may_enter_combat(actor).allowed
            and can_detect_remotely(actor, target).allowed
        )
    except (InjuryError, AttributeError, TypeError):
        return False


def _arrive_pursuit(
    actor: Any, target: Any, state: dict[str, Any]
) -> NavigationOutcome:
    from systems.attacks import can_attack
    from systems.combat import start_fight

    state["pursuit"] = None
    _write_state(actor, state)
    if _pursuit_target_allowed(actor, target) and can_attack(actor, target).allowed:
        start_fight(actor, target)
    return NavigationOutcome("skipped", "arrived")


def _clear_pursuit(actor: Any, state: dict[str, Any], reason: str) -> NavigationOutcome:
    state["pursuit"] = None
    _write_state(actor, state)
    return NavigationOutcome("no-route" if reason == "no_route" else "skipped", reason)


def _live_id(value: Any) -> bool:
    return isinstance(getattr(value, "id", None), int) and value.id > 0


def _resolve_character(object_id: int) -> Any | None:
    try:
        from typeclasses.characters import Character

        return Character.objects.get(id=object_id)
    except Exception:
        return None


def _validate_state(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "last_successful_token", "pursuit"}
        or raw.get("version") != MOBILE_NAVIGATION_VERSION
    ):
        raise ValueError("Mobile navigation state is invalid.")
    token, pursuit = raw["last_successful_token"], raw["pursuit"]
    if isinstance(token, bool) or not isinstance(token, int) or token < 0:
        raise ValueError("Mobile navigation state has invalid token data.")
    if pursuit is not None:
        if (
            not isinstance(pursuit, Mapping)
            or set(pursuit) != {"target_id", "remaining_steps"}
            or not isinstance(pursuit["target_id"], int)
            or pursuit["target_id"] < 1
            or not isinstance(pursuit["remaining_steps"], int)
            or not 0 <= pursuit["remaining_steps"] <= PURSUIT_STEP_LIMIT
        ):
            raise ValueError("Mobile navigation pursuit is invalid.")
        pursuit = dict(pursuit)
    return {
        "version": MOBILE_NAVIGATION_VERSION,
        "last_successful_token": token,
        "pursuit": pursuit,
    }


def _write_state(actor: Any, state: Mapping[str, Any]) -> None:
    actor.attributes.add(MOBILE_NAVIGATION_ATTRIBUTE, _validate_state(deepcopy(state)))

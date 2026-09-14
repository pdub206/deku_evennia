"""INTERACT-02C queued recall policy and execution."""

from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings
from systems.action_policy import Position
from systems.action_queue import (
    ActionDefinition,
    ActionQueueError,
    ActionResult,
    register_action_definition,
    schedule_action,
    unregister_action_definition,
)
from systems.combat import is_fighting
from systems.injury import InjuryError, InjuryState, injury_record
from systems.room_policy import admission_decision
from systems.room_roles import RoomRoleError, resolve_room_role

RECALL_ACTION_KEY = "interact02c.recall"
RECALL_CONFLICT_GROUP = "movement"
DEFAULT_RECALL_DELAY = 1


def schedule_recall(character: Any) -> ActionResult:
    """Queue recall after validating its source and configured destination."""
    source = character.location
    try:
        destination_id = resolve_room_role("RECALL_DESTINATION").room.id
    except RoomRoleError:
        destination_id = None
    args = {
        "source_id": getattr(source, "id", None),
        "destination_id": destination_id,
    }
    delay = getattr(settings, "INTERACTION_INTERVAL_ACTIONS", DEFAULT_RECALL_DELAY)
    return schedule_action(character, RECALL_ACTION_KEY, args, delay=delay)


def _validate(character: Any, args: Mapping[str, Any]) -> bool | str:
    """Apply the same complete recall policy at request and execution time."""
    if getattr(character.db, "is_player_character", None) is False:
        return "player_only"
    source = character.location
    if source is None or getattr(source, "id", None) != args.get("source_id"):
        return "source_changed"
    try:
        if injury_record(character).state is not InjuryState.CONSCIOUS:
            return "not_conscious"
    except InjuryError:
        return "invalid_injury"
    if character.actions.position is not Position.STANDING:
        return "not_standing"
    if is_fighting(character):
        return "in_combat"
    if not source.access(character, "recall", default=True):
        return "source_denies_recall"
    try:
        destination = resolve_room_role("RECALL_DESTINATION").room
    except RoomRoleError:
        return "destination_unavailable"
    if destination.id != args.get("destination_id"):
        return "destination_changed"
    decision = admission_decision(character, destination, mode="recall")
    return True if decision.allowed else decision.reason


def _execute(character: Any, args: Mapping[str, Any], reservation: Mapping[str, Any]):
    """Move exactly once without an entry hazard or remote output."""
    source = character.location
    destination = resolve_room_role("RECALL_DESTINATION").room
    if not character.move_to(
        destination,
        quiet=True,
        move_type="recall",
        trigger_entry_hazard=False,
    ):
        return "arrival_failed"
    source.msg_contents(f"{character.key} fades from view.", exclude=character)
    character.msg("You recall to safety.")
    destination.msg_contents(f"{character.key} fades into view.", exclude=character)
    return None


def _register() -> None:
    definition = ActionDefinition(
        RECALL_ACTION_KEY,
        RECALL_CONFLICT_GROUP,
        _validate,
        _execute,
        survive_reload=True,
        disconnect_safe=False,
    )
    try:
        register_action_definition(definition)
    except ActionQueueError:
        unregister_action_definition(RECALL_ACTION_KEY)
        register_action_definition(definition)


_register()

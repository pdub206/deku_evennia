"""GROUP-01B's post-commit PC follower travel propagation.

This consumer deliberately has no movement hook.  Only INTERACT-03's committed
``TravelCompletion`` event can schedule a follow attempt, which keeps recalls,
teleports, mobile movement, and direct relocation out of the graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from systems.action_queue import ActionStatus, inspect_action
from systems.travel import TravelCompletion, schedule_travel

FOLLOW_ATTEMPT_ATTRIBUTE = "group01_follow_attempts"
FOLLOW_ATTEMPT_VERSION = 1
FOLLOW_ATTEMPT_LIMIT = 32


def propagate_travel_completion(event: TravelCompletion) -> None:
    """Schedule each direct follower once, in stable order, after a commit."""
    from systems.player_following import direct_followers, pc_by_id

    leader = pc_by_id(event.actor_id)
    if leader is None:
        return
    for follower in direct_followers(leader):
        try:
            _schedule_one(follower, leader, event)
        except Exception:
            # Bad state on one edge must not interrupt later direct followers.
            propagation_failed(follower, leader, "invalid")


def propagation_failed(follower: Any, leader: Any | None, reason: str) -> None:
    """End one failed edge and notify exactly its two endpoints once."""
    from systems.player_following import follow_state, unlink

    try:
        if leader is None or follow_state(follower)["leader_id"] != leader.id:
            return
        unlink(leader, follower)
    except Exception:
        return
    follower.msg("You are left behind and stop following.")
    leader.msg(f"{follower.key} is left behind and stops following you.")


def _schedule_one(follower: Any, leader: Any, event: TravelCompletion) -> None:
    """Apply pre-queue edge checks, then reserve ordinary travel for one PC."""
    if _already_attempted(follower, event.action_id):
        return
    if not _eligible_departure(follower, leader, event):
        _remember_attempt(follower, event.action_id)
        propagation_failed(follower, leader, "ineligible")
        return
    result = schedule_travel(follower, _exit(event.exit_id), follow_event=event)
    if result.status not in {ActionStatus.QUEUED, ActionStatus.REPLACED}:
        _remember_attempt(follower, event.action_id)
        propagation_failed(follower, leader, result.reason)
        return
    _remember_attempt(follower, event.action_id)


def _eligible_departure(follower: Any, leader: Any, event: TravelCompletion) -> bool:
    """Check every relationship-only condition before queueing a travel action."""
    from systems.action_policy import Position
    from systems.combat import is_fighting
    from systems.combat_outcomes import InjuryState
    from systems.injury import injury_record
    from systems.player_following import follow_state
    from systems.visibility import room_visibility, target_visibility

    try:
        if follow_state(follower)["leader_id"] != leader.id:
            return False
        if getattr(follower.location, "id", None) != event.source_id:
            return False
        if injury_record(follower).state is not InjuryState.CONSCIOUS:
            return False
        if follower.actions.position is not Position.STANDING or is_fighting(follower):
            return False
        if inspect_action(follower) is not None:
            return False
        exit_obj = _exit(event.exit_id)
        return bool(
            exit_obj
            and room_visibility(follower, follower.location).visible
            and target_visibility(follower, exit_obj).visible
        )
    except Exception:
        return False


def _exit(exit_id: int) -> Any | None:
    """Resolve the recorded exit only at the point it is needed."""
    from evennia.objects.models import ObjectDB

    try:
        return ObjectDB.objects.get(id=exit_id)
    except (ObjectDB.DoesNotExist, TypeError, ValueError):
        return None


def _attempts(follower: Any) -> list[str]:
    """Read a bounded, primitive idempotency ledger, repairing invalid data."""
    raw = follower.attributes.get(FOLLOW_ATTEMPT_ATTRIBUTE)
    if not isinstance(raw, Mapping) or raw.get("version") != FOLLOW_ATTEMPT_VERSION:
        return []
    ids = raw.get("event_ids")
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        return []
    return ids[-FOLLOW_ATTEMPT_LIMIT:]


def _already_attempted(follower: Any, event_id: str) -> bool:
    """Return whether this edge has already consumed the completion event."""
    return event_id in _attempts(follower)


def _remember_attempt(follower: Any, event_id: str) -> None:
    """Persist consumption before reload can replay an event delivery."""
    if not isinstance(event_id, str) or not event_id:
        return
    event_ids = _attempts(follower)
    if event_id not in event_ids:
        event_ids.append(event_id)
    follower.attributes.add(
        FOLLOW_ATTEMPT_ATTRIBUTE,
        {
            "version": FOLLOW_ATTEMPT_VERSION,
            "event_ids": event_ids[-FOLLOW_ATTEMPT_LIMIT:],
        },
    )

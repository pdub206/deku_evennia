"""GROUP-01A's durable, consent-based PC follow graph.

This module owns PC-to-PC follow requests and edges.  It stores only primitive
ids on characters; MOB-07 remains the owner of every NPC relationship.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from systems.lifecycle import (
    CharacterAvailability,
    LifecycleConsumer,
    LifecycleError,
    ServerLifecycleEvent,
    ServerTransitionMode,
    ServerTransitionPhase,
    register_lifecycle_consumer,
    unregister_lifecycle_consumer,
)

FOLLOW_ATTRIBUTE = "group01_follow"
FOLLOW_VERSION = 1
FOLLOW_CAPACITY = 8
FOLLOW_LIFECYCLE_KEY = "group01.following"


@dataclass(frozen=True)
class FollowOutcome:
    """One side-effect-safe result returned by a follow graph operation."""

    status: str
    reason: str = ""

    @property
    def accepted(self) -> bool:
        """Whether the requested operation changed or already matched state."""
        return self.status == "accepted"


def follow_state(character: Any) -> dict[str, Any]:
    """Return a detached validated record for one PC."""
    raw = character.attributes.get(FOLLOW_ATTRIBUTE)
    if raw is None:
        return _initial_state()
    return _validate_state(raw)


def request(follower: Any, leader: Any) -> FollowOutcome:
    """Create or replace a consent request after endpoint validation."""
    if not _eligible(follower) or not _eligible(leader):
        return FollowOutcome("denied", "invalid_member")
    if follower is leader:
        return FollowOutcome("denied", "self_link")
    if follower.location is not leader.location:
        return FollowOutcome("denied", "not_colocated")
    try:
        follower_state, leader_state = follow_state(follower), follow_state(leader)
    except ValueError:
        return FollowOutcome("denied", "malformed_state")
    if follower_state["leader_id"] == leader.id:
        return FollowOutcome("denied", "already_following")
    if follower_state["request_id"] == leader.id:
        return FollowOutcome("accepted", "already_requested")
    if (
        _inbound_request_count(leader) >= FOLLOW_CAPACITY
        and leader_state.get("request_id") != leader.id
    ):
        return FollowOutcome("denied", "request_capacity")
    follower_state["request_id"] = leader.id
    follower_state["sequence"] += 1
    _write(follower, follower_state)
    return FollowOutcome("accepted", "requested")


def accept(leader: Any, follower: Any) -> FollowOutcome:
    """Accept a matching request, atomically revalidating both full graphs."""
    if not _eligible(leader) or not _eligible(follower):
        return FollowOutcome("denied", "invalid_member")
    try:
        follower_state = follow_state(follower)
    except ValueError:
        return FollowOutcome("denied", "malformed_state")
    if follower_state["request_id"] != leader.id:
        return FollowOutcome("denied", "no_request")
    if follower.location is not leader.location:
        return FollowOutcome("denied", "not_colocated")
    if follower_state["leader_id"] is not None:
        return FollowOutcome("denied", "already_following")
    if _would_cycle(follower.id, leader.id):
        return FollowOutcome("denied", "cycle")
    members = _connected_members(follower.id) | _connected_members(leader.id)
    if len(members) > FOLLOW_CAPACITY:
        return FollowOutcome("denied", "capacity")
    follower_state.update(leader_id=leader.id, request_id=None)
    follower_state["sequence"] += 1
    _write(follower, follower_state)
    return FollowOutcome("accepted", "linked")


def decline(leader: Any, follower: Any) -> FollowOutcome:
    """End a matching request without creating a follow edge."""
    try:
        state = follow_state(follower)
    except ValueError:
        return FollowOutcome("denied", "malformed_state")
    if state["request_id"] != getattr(leader, "id", None):
        return FollowOutcome("denied", "no_request")
    state["request_id"] = None
    state["sequence"] += 1
    _write(follower, state)
    return FollowOutcome("accepted", "declined")


def unlink(actor: Any, follower: Any | None = None) -> FollowOutcome:
    """Remove the caller's edge, or one direct follower edge without consent."""
    follower = actor if follower is None else follower
    try:
        state = follow_state(follower)
    except ValueError:
        return FollowOutcome("denied", "malformed_state")
    if state["leader_id"] is None:
        return FollowOutcome("accepted", "already_unlinked")
    if follower is not actor and state["leader_id"] != getattr(actor, "id", None):
        return FollowOutcome("denied", "not_direct_follower")
    state["leader_id"] = None
    state["sequence"] += 1
    _write(follower, state)
    return FollowOutcome("accepted", "unlinked")


def inspect(character: Any) -> dict[str, Any]:
    """Return a detached, staff-safe graph snapshot rooted at a PC."""
    state = follow_state(character)
    return {"state": state, "members": tuple(sorted(_connected_members(character.id)))}


def repair(character: Any | None = None) -> int:
    """Clear malformed, dangling, cyclic, and over-capacity PC follow records."""
    changed = 0
    characters = (character,) if character is not None else _pcs()
    valid_ids = {pc.id for pc in _pcs()}
    for pc in characters:
        state_changed = False
        try:
            state = follow_state(pc)
        except ValueError:
            _write(pc, _initial_state())
            changed += 1
            continue
        if state["leader_id"] not in valid_ids:
            if state["leader_id"] is not None:
                state["leader_id"] = None
                changed += 1
                state_changed = True
        if state["request_id"] not in valid_ids:
            if state["request_id"] is not None:
                state["request_id"] = None
                changed += 1
                state_changed = True
        if state["leader_id"] == pc.id or state["request_id"] == pc.id:
            state["leader_id"] = state["request_id"] = None
            changed += 1
            state_changed = True
        if state_changed:
            state["sequence"] += 1
            _write(pc, state)
    # A deterministic pass removes every edge that cannot remain legal.
    for pc in _pcs():
        try:
            state = follow_state(pc)
            if state["leader_id"] is not None and (
                _would_cycle(pc.id, state["leader_id"], exclude=pc.id)
                or len(_connected_members(pc.id)) > FOLLOW_CAPACITY
            ):
                state["leader_id"] = None
                state["sequence"] += 1
                _write(pc, state)
                changed += 1
        except ValueError:
            continue
    return changed


def clear_for(character: Any) -> None:
    """End every request and edge touching a PC, idempotently."""
    character_id = getattr(character, "id", None)
    for pc in _pcs():
        try:
            state = follow_state(pc)
        except ValueError:
            continue
        if pc is character or character_id in {state["leader_id"], state["request_id"]}:
            state["leader_id"] = (
                None
                if pc is character or state["leader_id"] == character_id
                else state["leader_id"]
            )
            state["request_id"] = (
                None
                if pc is character or state["request_id"] == character_id
                else state["request_id"]
            )
            state["sequence"] += 1
            _write(pc, state)


def clear_requests_for_move(character: Any) -> None:
    """End requests involving a PC whenever either endpoint changes rooms."""
    clear_requests_for(character)


def clear_requests_for(character: Any) -> None:
    """Clear pending requests touching a character while retaining follow edges."""
    for pc in _pcs():
        try:
            state = follow_state(pc)
        except ValueError:
            continue
        if pc is character or state["request_id"] == getattr(character, "id", None):
            if state["request_id"] is not None:
                state["request_id"] = None
                state["sequence"] += 1
                _write(pc, state)


def _initial_state() -> dict[str, Any]:
    return {
        "version": FOLLOW_VERSION,
        "leader_id": None,
        "request_id": None,
        "sequence": 0,
    }


def _validate_state(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != set(_initial_state())
        or raw.get("version") != FOLLOW_VERSION
    ):
        raise ValueError("Player follow state is invalid.")
    state = deepcopy(dict(raw))
    for key in ("leader_id", "request_id"):
        if state[key] is not None and not _positive_id(state[key]):
            raise ValueError("Player follow endpoint is invalid.")
    if (
        not isinstance(state["sequence"], int)
        or isinstance(state["sequence"], bool)
        or state["sequence"] < 0
    ):
        raise ValueError("Player follow sequence is invalid.")
    return state


def _write(character: Any, state: Mapping[str, Any]) -> None:
    character.attributes.add(FOLLOW_ATTRIBUTE, _validate_state(state))


def _pcs() -> tuple[Any, ...]:
    try:
        from typeclasses.characters import Character

        return tuple(
            pc
            for pc in Character.objects.filter_family().iterator()
            if getattr(pc.db, "is_player_character", None) is not False
        )
    except Exception:
        return ()


def _eligible(character: Any) -> bool:
    if character not in _pcs() or getattr(character, "location", None) is None:
        return False
    try:
        from systems.combat_outcomes import InjuryState
        from systems.injury import injury_record

        return injury_record(character).state is InjuryState.CONSCIOUS
    except Exception:
        return False


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _inbound_request_count(leader: Any) -> int:
    return sum(follow_state(pc)["request_id"] == leader.id for pc in _pcs())


def _edges(*, exclude: int | None = None) -> dict[int, set[int]]:
    edges: dict[int, set[int]] = {}
    for pc in _pcs():
        if pc.id == exclude:
            continue
        try:
            leader_id = follow_state(pc)["leader_id"]
            if leader_id is not None:
                edges.setdefault(pc.id, set()).add(leader_id)
        except ValueError:
            continue
    # Read MOB-07 edges as graph facts, never write mobile state here.
    try:
        from systems.mobile_relationships import _npcs, relationship_state

        for npc in _npcs():
            state = relationship_state(npc)
            for target in (
                state["owner_id"],
                state["follow"] and state["follow"]["target_id"],
                state["charm"] and state["charm"]["controller_id"],
            ):
                if target is not None:
                    edges.setdefault(npc.id, set()).add(target)
    except Exception:
        pass
    return edges


def _would_cycle(source: int, target: int, *, exclude: int | None = None) -> bool:
    edges = _edges(exclude=exclude)
    edges.setdefault(source, set()).add(target)
    pending, seen = [target], set()
    while pending:
        current = pending.pop()
        if current == source:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(edges.get(current, ()))
    return False


def _connected_members(character_id: int) -> set[int]:
    edges = _edges()
    undirected: dict[int, set[int]] = {}
    for source, targets in edges.items():
        for target in targets:
            undirected.setdefault(source, set()).add(target)
            undirected.setdefault(target, set()).add(source)
    pending, members = [character_id], set()
    while pending:
        current = pending.pop()
        if current not in members:
            members.add(current)
            pending.extend(undirected.get(current, ()))
    return {member for member in members if member in {pc.id for pc in _pcs()}}


def _on_character(event: Any) -> None:
    if event.availability is CharacterAvailability.UNAVAILABLE:
        clear_for(event.character)


def _on_server(event: ServerLifecycleEvent) -> None:
    if event.phase is ServerTransitionPhase.RECOVER:
        if event.mode is ServerTransitionMode.COLD_RESTART:
            for pc in _pcs():
                _write(pc, _initial_state())
        else:
            repair()


def _register_lifecycle_consumer() -> None:
    consumer = LifecycleConsumer(
        FOLLOW_LIFECYCLE_KEY, on_character=_on_character, on_server=_on_server
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(FOLLOW_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


_register_lifecycle_consumer()

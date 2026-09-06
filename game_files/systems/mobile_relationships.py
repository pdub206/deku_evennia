"""MOB-07 pet, charm, following, and restricted-order relationships.

Only primitive ids are stored on a mobile.  This module is intentionally the
sole interpreter of that record, so combat rewards, lifecycle cleanup, and
movement do not grow competing owner/controller conventions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from systems.action_policy import ActionCategory
from systems.lifecycle import (CharacterAvailability, LifecycleConsumer,
                               LifecycleError, register_lifecycle_consumer,
                               unregister_lifecycle_consumer)

MOBILE_RELATIONSHIP_ATTRIBUTE = "mobile_relationship"
MOBILE_RELATIONSHIP_VERSION = 1
RELATIONSHIP_LIFECYCLE_KEY = "mob07.relationships"
FOLLOW_STEP_LIMIT = 8
DEFAULT_PET_CAPACITY = 1


@dataclass(frozen=True)
class RelationshipOutcome:
    """A safe, structured result from one relationship operation."""

    status: str
    reason: str = ""

    @property
    def accepted(self) -> bool:
        """Return whether the requested mutation or order was accepted."""
        return self.status in {"accepted", "acted", "queued"}


def relationship_state(npc: Any) -> dict[str, Any]:
    """Return a detached validated relationship snapshot for one NPC."""
    raw = npc.attributes.get(MOBILE_RELATIONSHIP_ATTRIBUTE)
    if raw is None:
        return _initial_state()
    return _validate_state(raw)


def acquire_pet(owner: Any, npc: Any) -> RelationshipOutcome:
    """Create durable ownership after final eligibility and capacity checks."""
    if not _valid_owner(owner) or not _valid_npc(npc):
        return RelationshipOutcome("denied", "invalid_member")
    if owner is npc:
        return RelationshipOutcome("denied", "self_link")
    if not _access(npc, owner, "pet"):
        return RelationshipOutcome("denied", "ownership_denied")
    try:
        state = relationship_state(npc)
    except ValueError:
        return RelationshipOutcome("denied", "malformed_state")
    if state["owner_id"] is not None:
        return RelationshipOutcome("denied", "already_owned")
    if _owned_count(owner) >= _capacity(owner):
        return RelationshipOutcome("denied", "capacity")
    if _would_cycle(npc.id, owner.id):
        return RelationshipOutcome("denied", "cycle")
    state["owner_id"] = owner.id
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "acquired")


def transfer_pet(owner: Any, npc: Any, recipient: Any) -> RelationshipOutcome:
    """Transfer durable ownership without allowing a charm takeover."""
    if not _valid_owner(recipient) or not _is_owner(owner, npc):
        return RelationshipOutcome("denied", "not_owner")
    if not _access(npc, owner, "transfer") or not _access(npc, recipient, "pet"):
        return RelationshipOutcome("denied", "ownership_denied")
    if _owned_count(recipient) >= _capacity(recipient):
        return RelationshipOutcome("denied", "capacity")
    if _would_cycle(npc.id, recipient.id):
        return RelationshipOutcome("denied", "cycle")
    state = relationship_state(npc)
    state["owner_id"] = recipient.id
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "transferred")


def charm(
    npc: Any, controller: Any, *, source_id: int | None = None, source_key: str = ""
) -> RelationshipOutcome:
    """Install temporary control while retaining any durable pet owner."""
    if not _valid_npc(npc) or not _valid_owner(controller):
        return RelationshipOutcome("denied", "invalid_member")
    if not _access(npc, controller, "charm"):
        return RelationshipOutcome("denied", "control_denied")
    if source_id is not None and not _positive_id(source_id):
        return RelationshipOutcome("denied", "invalid_source")
    if not isinstance(source_key, str) or len(source_key) > 80:
        return RelationshipOutcome("denied", "invalid_source")
    if _would_cycle(npc.id, controller.id):
        return RelationshipOutcome("denied", "cycle")
    state = relationship_state(npc)
    current = state["charm"]
    if current is not None and current["controller_id"] != controller.id:
        return RelationshipOutcome("denied", "already_controlled")
    state["charm"] = {
        "controller_id": controller.id,
        "source_id": source_id,
        "source_key": source_key,
    }
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "charmed")


def release_charm(npc: Any, *, source_id: int | None = None) -> RelationshipOutcome:
    """Release a matching charm exactly once, exposing underlying ownership."""
    try:
        state = relationship_state(npc)
    except ValueError:
        return RelationshipOutcome("denied", "malformed_state")
    charm_state = state["charm"]
    if charm_state is None or (
        source_id is not None and charm_state["source_id"] != source_id
    ):
        return RelationshipOutcome("accepted", "already_released")
    state["charm"] = None
    state["follow"] = None
    state["pending_order"] = None
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "released_charm")


def dismiss(actor: Any, npc: Any) -> RelationshipOutcome:
    """Idempotently clear pet ownership, control, following, and orders."""
    state = relationship_state(npc)
    if state["owner_id"] is None:
        return RelationshipOutcome("accepted", "already_dismissed")
    if not _is_owner(actor, npc):
        return RelationshipOutcome("denied", "not_owner")
    state.update(owner_id=None, charm=None, follow=None, pending_order=None)
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "dismissed")


def effective_controller(npc: Any) -> Any | None:
    """Resolve valid charm control first, then durable ownership."""
    try:
        state = relationship_state(npc)
    except ValueError:
        return None
    controller_id = (
        state["charm"]["controller_id"] if state["charm"] else state["owner_id"]
    )
    controller = _character(controller_id)
    return controller if _valid_owner(controller) else None


def responsible_pc_id(npc: Any) -> int | None:
    """Snapshot the effective responsible PC for COMBAT-07 attribution."""
    controller = effective_controller(npc)
    return controller.id if controller is not None else None


def set_following(
    actor: Any, npc: Any, leader: Any | None = None
) -> RelationshipOutcome:
    """Set a controlled NPC's transient follow edge, rejecting graph cycles."""
    if effective_controller(npc) is not actor:
        return RelationshipOutcome("denied", "not_controller")
    if not _can_act(actor) or not _can_act(npc, ActionCategory.MOVE):
        return RelationshipOutcome("denied", "action_denied")
    if leader is None:
        leader = actor
    if not _valid_character(leader) or leader is npc or _would_cycle(npc.id, leader.id):
        return RelationshipOutcome("denied", "cycle")
    state = relationship_state(npc)
    state["follow"] = {"target_id": leader.id, "remaining_steps": FOLLOW_STEP_LIMIT}
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "following")


def stay(actor: Any, npc: Any) -> RelationshipOutcome:
    """Clear a controlled NPC's transient following intent."""
    if effective_controller(npc) is not actor:
        return RelationshipOutcome("denied", "not_controller")
    state = relationship_state(npc)
    state["follow"] = None
    state["pending_order"] = None
    state["sequence"] += 1
    _write_state(npc, state)
    return RelationshipOutcome("accepted", "staying")


def note_leader_moved(leader: Any, source: Any) -> None:
    """Give each valid follower one bounded next-step intent after a move."""
    if source is None or leader.location is source:
        return
    for npc in _npcs():
        try:
            state = relationship_state(npc)
            follow = state["follow"]
            if follow is not None and follow["target_id"] == getattr(
                leader, "id", None
            ):
                follow["remaining_steps"] = FOLLOW_STEP_LIMIT
                state["follow"] = follow
                _write_state(npc, state)
        except Exception:
            continue


def advance_follow(npc: Any, token: int) -> RelationshipOutcome | None:
    """Move a follower at most one legal MOB-04 step for its consumed token."""
    try:
        state = relationship_state(npc)
    except ValueError:
        return RelationshipOutcome("failed", "malformed_state")
    follow = state["follow"]
    if follow is None:
        return None
    leader = _character(follow["target_id"])
    if effective_controller(npc) is None or not _valid_character(leader):
        state["follow"] = None
        _write_state(npc, state)
        return RelationshipOutcome("skipped", "target_lost")
    if npc.location is leader.location:
        return RelationshipOutcome("skipped", "arrived")
    if follow["remaining_steps"] <= 0:
        state["follow"] = None
        _write_state(npc, state)
        return RelationshipOutcome("skipped", "route_bound")
    from systems.mobile_navigation import follow_step

    outcome = follow_step(npc, leader, token)
    if outcome.status == "moved":
        follow["remaining_steps"] -= 1
        state["follow"] = follow
        _write_state(npc, state)
        return RelationshipOutcome("acted", "followed")
    if outcome.status in {"blocked", "no-route", "failed"}:
        state["follow"] = None
        _write_state(npc, state)
    return RelationshipOutcome("skipped", outcome.reason or outcome.status)


def order(actor: Any, npc: Any, command_text: str) -> RelationshipOutcome:
    """Run one normal pet command after control and command-lock revalidation.

    The text is deliberately handled by the pet's ordinary Character command
    set, not parsed or reimplemented here. That keeps ``get all corpse``,
    ``put item bag``, exits, and future player commands consistent while their
    normal locks and action policies continue to run as the pet. ``order``
    itself is excluded to prevent recursive command chains.
    """
    if not isinstance(command_text, str) or not 1 <= len(command_text.strip()) <= 500:
        return RelationshipOutcome("denied", "invalid_command")
    command_key = command_text.strip().split(None, 1)[0].casefold()
    if command_key == "order":
        return RelationshipOutcome("denied", "nested_order")
    if effective_controller(npc) is not actor or npc.location is not actor.location:
        return RelationshipOutcome("denied", "not_controlled_here")
    if (
        not _access(npc, actor, "order")
        or not _can_act(actor, ActionCategory.STATE_INDEPENDENT)
        or not _can_act(npc, ActionCategory.STATE_INDEPENDENT)
    ):
        return RelationshipOutcome("denied", "action_denied")
    try:
        npc.execute_cmd(command_text.strip())
    except Exception:
        return RelationshipOutcome("failed", "command_failed")
    return RelationshipOutcome("acted", "command")


def repair_relationships_for(character: Any) -> None:
    """Clear every dangling edge touching a permanently unavailable character."""
    character_id = getattr(character, "id", None)
    if not _positive_id(character_id):
        return
    for npc in _npcs():
        try:
            state = relationship_state(npc)
        except ValueError:
            continue
        charm_state, follow = state["charm"], state["follow"]
        changed = npc is character or state["owner_id"] == character_id
        changed = changed or (
            charm_state is not None and charm_state["controller_id"] == character_id
        )
        changed = changed or (
            follow is not None and follow["target_id"] == character_id
        )
        if changed:
            state.update(owner_id=None, charm=None, follow=None, pending_order=None)
            state["sequence"] += 1
            _write_state(npc, state)


def _initial_state() -> dict[str, Any]:
    return {
        "version": MOBILE_RELATIONSHIP_VERSION,
        "owner_id": None,
        "charm": None,
        "follow": None,
        "pending_order": None,
        "sequence": 0,
    }


def _validate_state(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != set(_initial_state())
        or raw.get("version") != MOBILE_RELATIONSHIP_VERSION
    ):
        raise ValueError("Mobile relationship state is invalid.")
    owner, charm_state, follow, pending, sequence = (
        raw["owner_id"],
        raw["charm"],
        raw["follow"],
        raw["pending_order"],
        raw["sequence"],
    )
    if owner is not None and not _positive_id(owner):
        raise ValueError("Mobile relationship owner is invalid.")
    if charm_state is not None and (
        not isinstance(charm_state, Mapping)
        or set(charm_state) != {"controller_id", "source_id", "source_key"}
        or not _positive_id(charm_state["controller_id"])
        or (
            charm_state["source_id"] is not None
            and not _positive_id(charm_state["source_id"])
        )
        or not isinstance(charm_state["source_key"], str)
    ):
        raise ValueError("Mobile charm control is invalid.")
    if follow is not None and (
        not isinstance(follow, Mapping)
        or set(follow) != {"target_id", "remaining_steps"}
        or not _positive_id(follow["target_id"])
        or not isinstance(follow["remaining_steps"], int)
        or not 0 <= follow["remaining_steps"] <= FOLLOW_STEP_LIMIT
    ):
        raise ValueError("Mobile follow intent is invalid.")
    if (
        pending is not None
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
    ):
        raise ValueError("Mobile relationship runtime state is invalid.")
    return deepcopy(dict(raw))


def _write_state(npc: Any, state: Mapping[str, Any]) -> None:
    npc.attributes.add(MOBILE_RELATIONSHIP_ATTRIBUTE, _validate_state(state))


def _is_owner(actor: Any, npc: Any) -> bool:
    try:
        return _valid_owner(actor) and relationship_state(npc)["owner_id"] == actor.id
    except (AttributeError, ValueError):
        return False


def _valid_character(value: Any) -> bool:
    return (
        _positive_id(getattr(value, "id", None))
        and getattr(value, "location", None) is not None
    )


def _valid_owner(value: Any) -> bool:
    return (
        _valid_character(value)
        and getattr(value.db, "is_player_character", None) is not False
    )


def _valid_npc(value: Any) -> bool:
    return (
        _valid_character(value)
        and getattr(value.db, "is_player_character", None) is False
    )


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _access(npc: Any, actor: Any, access_type: str) -> bool:
    try:
        return npc.access(actor, access_type, default=True)
    except Exception:
        return False


def _can_act(actor: Any, category: ActionCategory = ActionCategory.MANIPULATE) -> bool:
    try:
        return actor.actions.check(category).allowed
    except Exception:
        return False


def _character(object_id: int | None) -> Any | None:
    if not _positive_id(object_id):
        return None
    try:
        from typeclasses.characters import Character

        return Character.objects.get(id=object_id)
    except Exception:
        return None


def _npcs() -> tuple[Any, ...]:
    try:
        from typeclasses.characters import Character

        return tuple(
            character
            for character in Character.objects.filter_family().iterator()
            if getattr(character.db, "is_player_character", None) is False
        )
    except Exception:
        return ()


def _owned_count(owner: Any) -> int:
    return sum(1 for npc in _npcs() if relationship_state(npc)["owner_id"] == owner.id)


def _capacity(owner: Any) -> int:
    value = owner.attributes.get("pet_capacity", DEFAULT_PET_CAPACITY)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _would_cycle(source_id: int, target_id: int) -> bool:
    """Return whether adding source -> target closes any relationship cycle."""
    edges: dict[int, set[int]] = {}
    for npc in _npcs():
        try:
            state = relationship_state(npc)
        except ValueError:
            continue
        targets = {
            value
            for value in (
                state["owner_id"],
                state["follow"] and state["follow"]["target_id"],
                state["charm"] and state["charm"]["controller_id"],
            )
            if value is not None
        }
        edges[npc.id] = targets
    edges.setdefault(source_id, set()).add(target_id)
    stack, visited = [target_id], set()
    while stack:
        current = stack.pop()
        if current == source_id:
            return True
        if current not in visited:
            visited.add(current)
            stack.extend(edges.get(current, ()))
    return False


def _on_character_lifecycle(event: Any) -> None:
    if event.availability is CharacterAvailability.UNAVAILABLE:
        # Disconnect preserves ownership, but transient following and unsafe
        # work cannot survive an absent controller.
        for npc in _npcs():
            try:
                state = relationship_state(npc)
                if state["owner_id"] == event.character.id or (
                    state["charm"]
                    and state["charm"]["controller_id"] == event.character.id
                ):
                    state["follow"] = None
                    state["pending_order"] = None
                    _write_state(npc, state)
            except Exception:
                continue


def _register_lifecycle_consumer() -> None:
    consumer = LifecycleConsumer(
        RELATIONSHIP_LIFECYCLE_KEY, on_character=_on_character_lifecycle
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(RELATIONSHIP_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


_register_lifecycle_consumer()

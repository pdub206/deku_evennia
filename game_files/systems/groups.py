"""GROUP-02's durable party registry, lifecycle policy, and safe status readers.

The registry intentionally stores only primitive identifiers.  It does not
create follow edges or confer access; its sole cross-system policy is the
side-effect-free ``are_allied`` reader used by hostile-action authorization.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from evennia.server.models import ServerConfig
from systems.lifecycle import (
    CharacterAvailability,
    LifecycleConsumer,
    LifecycleError,
    UnavailabilityCause,
    is_character_unavailable,
    register_lifecycle_consumer,
    unregister_lifecycle_consumer,
)

GROUP_CONFIG_KEY = "group02_registry"
GROUP_VERSION = 1
GROUP_CAPACITY = 8
GROUP_LIFECYCLE_KEY = "group02.lifecycle"


@dataclass(frozen=True)
class GroupOutcome:
    """A player-neutral result from one group operation."""

    accepted: bool
    changed: bool = False
    reason: str = ""


def group_for(character: Any) -> dict[str, Any] | None:
    """Return a detached group record containing this PC, if any."""
    identifier = _id(character)
    if identifier is None:
        return None
    state = _read()
    for group_id, group in state["groups"].items():
        if identifier in group["members"]:
            return {"id": int(group_id), **deepcopy(group)}
    return None


def group_members(character: Any) -> tuple[int, ...]:
    """Return ordered member ids for a PC's current group."""
    group = group_for(character)
    return tuple(group["members"]) if group else ()


def reward_roster(character: Any) -> dict[str, Any] | None:
    """Snapshot ``character``'s current party for a combat-reward ledger.

    The result contains primitives only.  It deliberately captures the registry
    mutation sequence and member order at contribution time, rather than
    looking up a party after the victim dies.
    """
    identifier = _id(character)
    if identifier is None:
        return None
    state = _read()
    found = _group_by_member(state, identifier)
    if found is None:
        return None
    group_id, group = found
    return {
        "group_id": int(group_id),
        "membership_sequence": state["sequence"],
        "member_ids": tuple(group["members"]),
    }


def status_lines(character: Any) -> tuple[str, ...] | None:
    """Return consented, privacy-safe status rows for one current group.

    This reader intentionally degrades missing or malformed member state to
    bounded labels. It never exposes a room key, dbref, exact resources, or a
    lifecycle diagnostic.
    """
    group = group_for(character)
    if group is None:
        return None
    lines = ["Leader: " + _member_name(group["leader_id"], character)]
    for member_id in group["members"]:
        member = _pc_by_id(member_id)
        lines.append(
            " | ".join(
                (
                    _member_name(member_id, character),
                    _member_class_level(member),
                    _member_presence(member),
                    _member_room(member, character),
                    _member_position(member),
                    _member_health(member),
                )
            )
        )
    return tuple(lines)


def are_allied(first: Any, second: Any) -> bool:
    """Return whether two PCs share a current party, without mutation."""
    first_id, second_id = _id(first), _id(second)
    if first_id is None or second_id is None:
        return False
    return any(
        first_id in group["members"] and second_id in group["members"]
        for group in _read()["groups"].values()
    )


def invite(leader: Any, target: Any) -> GroupOutcome:
    """Create or replace one same-room invitation from a party leader."""
    with transaction.atomic():
        state = _locked()
        leader_id, target_id = _id(leader), _id(target)
        if not _invite_eligible(leader, target) or leader_id == target_id:
            return GroupOutcome(False, reason="ineligible")
        leader_group = _group_by_member(state, leader_id)
        if leader_group is not None and leader_group[1]["leader_id"] != leader_id:
            return GroupOutcome(False, reason="not_leader")
        if _group_by_member(state, target_id) is not None:
            return GroupOutcome(False, reason="target_grouped")
        invitations = state["invitations"]
        pair = next(
            (
                entry
                for entry in invitations
                if entry["leader_id"] == leader_id and entry["target_id"] == target_id
            ),
            None,
        )
        if pair is not None:
            return GroupOutcome(True, reason="already_invited")
        if (
            sum(entry["leader_id"] == leader_id for entry in invitations)
            >= GROUP_CAPACITY
        ):
            return GroupOutcome(False, reason="invitation_capacity")
        if (
            sum(entry["target_id"] == target_id for entry in invitations)
            >= GROUP_CAPACITY
        ):
            return GroupOutcome(False, reason="invitation_capacity")
        state["sequence"] += 1
        invitations.append(
            {
                "leader_id": leader_id,
                "target_id": target_id,
                "sequence": state["sequence"],
            }
        )
        _write(state)
        return GroupOutcome(True, True, "invited")


def accept(target: Any, leader: Any) -> GroupOutcome:
    """Accept an invitation, atomically rechecking all membership constraints."""
    with transaction.atomic():
        state = _locked()
        leader_id, target_id = _id(leader), _id(target)
        invitation = next(
            (
                entry
                for entry in state["invitations"]
                if entry["leader_id"] == leader_id and entry["target_id"] == target_id
            ),
            None,
        )
        if invitation is None:
            return GroupOutcome(False, reason="no_invitation")
        if not _invite_eligible(leader, target):
            return GroupOutcome(False, reason="ineligible")
        if _group_by_member(state, target_id) is not None:
            return GroupOutcome(False, reason="target_grouped")
        leader_group = _group_by_member(state, leader_id)
        if leader_group is not None and leader_group[1]["leader_id"] != leader_id:
            return GroupOutcome(False, reason="not_leader")
        members = leader_group[1]["members"] if leader_group else [leader_id]
        if len(members) >= GROUP_CAPACITY:
            return GroupOutcome(False, reason="capacity")
        if _any_fighting(*members, target_id):
            return GroupOutcome(False, reason="fighting")
        state["sequence"] += 1
        if leader_group is None:
            group_id = state["next_id"]
            state["next_id"] += 1
            state["groups"][str(group_id)] = {
                "leader_id": leader_id,
                "members": [leader_id, target_id],
                "joins": {
                    str(leader_id): state["sequence"],
                    str(target_id): state["sequence"] + 1,
                },
            }
            state["sequence"] += 1
        else:
            leader_group[1]["members"].append(target_id)
            leader_group[1]["joins"][str(target_id)] = state["sequence"]
        _remove_invitations(state, target_id=target_id)
        _write(state)
        return GroupOutcome(True, True, "joined")


def decline(target: Any, leader: Any) -> GroupOutcome:
    """Remove exactly the requested invitation."""
    with transaction.atomic():
        state = _locked()
        before = len(state["invitations"])
        state["invitations"] = [
            entry
            for entry in state["invitations"]
            if not (
                entry["leader_id"] == _id(leader) and entry["target_id"] == _id(target)
            )
        ]
        if len(state["invitations"]) == before:
            return GroupOutcome(False, reason="no_invitation")
        state["sequence"] += 1
        _write(state)
        return GroupOutcome(True, True, "declined")


def leave(member: Any) -> GroupOutcome:
    """Let a nonleader leave; leaders must transfer or disband explicitly."""
    with transaction.atomic():
        state = _locked()
        found = _group_by_member(state, _id(member))
        if found is None:
            return GroupOutcome(False, reason="not_grouped")
        _, group = found
        if group["leader_id"] == _id(member):
            return GroupOutcome(False, reason="leader_must_transfer")
        return _remove_member(state, _id(member))


def kick(leader: Any, member: Any) -> GroupOutcome:
    """Remove one nonleader member under the leader's authority."""
    with transaction.atomic():
        state = _locked()
        found = _group_by_member(state, _id(leader))
        if found is None or found[1]["leader_id"] != _id(leader):
            return GroupOutcome(False, reason="not_leader")
        if _id(member) == _id(leader) or _id(member) not in found[1]["members"]:
            return GroupOutcome(False, reason="not_member")
        return _remove_member(state, _id(member))


def transfer_leader(leader: Any, member: Any) -> GroupOutcome:
    """Transfer leadership without changing membership, including in combat."""
    with transaction.atomic():
        state = _locked()
        found = _group_by_member(state, _id(leader))
        if found is None or found[1]["leader_id"] != _id(leader):
            return GroupOutcome(False, reason="not_leader")
        if _id(member) not in found[1]["members"]:
            return GroupOutcome(False, reason="not_member")
        if _id(member) == _id(leader):
            return GroupOutcome(True, reason="already_leader")
        found[1]["leader_id"] = _id(member)
        # Invitations are authority-specific: the former leader cannot leave
        # offers outstanding after no longer being authorized to form a party.
        _remove_invitations(state, leader_id=_id(leader))
        state["sequence"] += 1
        _write(state)
        return GroupOutcome(True, True, "transferred")


def disband(leader: Any) -> GroupOutcome:
    """Remove a leader's party when no affected member is fighting."""
    with transaction.atomic():
        state = _locked()
        found = _group_by_member(state, _id(leader))
        if found is None or found[1]["leader_id"] != _id(leader):
            return GroupOutcome(False, reason="not_leader")
        group_id, group = found
        if _any_fighting(*group["members"]):
            return GroupOutcome(False, reason="fighting")
        del state["groups"][group_id]
        _remove_invitations(state, leader_id=_id(leader))
        state["sequence"] += 1
        _write(state)
        return GroupOutcome(True, True, "disbanded")


def remove_deleted(character: Any) -> None:
    """Permanently remove a deleted PC and promote earliest surviving member."""
    with transaction.atomic():
        state = _locked()
        identifier = _id(character)
        found = _group_by_member(state, identifier)
        changed = False
        if found is not None:
            group_id, group = found
            group["members"].remove(identifier)
            group["joins"].pop(str(identifier), None)
            if not group["members"]:
                del state["groups"][group_id]
            elif group["leader_id"] == identifier:
                group["leader_id"] = min(
                    group["members"], key=lambda member: group["joins"][str(member)]
                )
            changed = True
        before = len(state["invitations"])
        _remove_invitations(state, leader_id=identifier, target_id=identifier)
        if changed or len(state["invitations"]) != before:
            state["sequence"] += 1
            _write(state)


def cancel_invitations(character: Any) -> None:
    """End invitations involving a permanently unavailable PC, idempotently."""
    with transaction.atomic():
        state = _locked()
        before = len(state["invitations"])
        _remove_invitations(state, leader_id=_id(character), target_id=_id(character))
        if len(state["invitations"]) != before:
            state["sequence"] += 1
            _write(state)


def _remove_member(state: dict[str, Any], identifier: int | None) -> GroupOutcome:
    found = _group_by_member(state, identifier)
    if found is None or identifier is None:
        return GroupOutcome(False, reason="not_member")
    group_id, group = found
    if _any_fighting(*group["members"]):
        return GroupOutcome(False, reason="fighting")
    group["members"].remove(identifier)
    group["joins"].pop(str(identifier), None)
    if not group["members"]:
        del state["groups"][group_id]
    _remove_invitations(state, target_id=identifier)
    state["sequence"] += 1
    _write(state)
    return GroupOutcome(True, True, "left")


def _remove_invitations(
    state: dict[str, Any], *, leader_id: int | None = None, target_id: int | None = None
) -> None:
    state["invitations"] = [
        entry
        for entry in state["invitations"]
        if (leader_id is None or entry["leader_id"] != leader_id)
        and (target_id is None or entry["target_id"] != target_id)
    ]


def _invite_eligible(leader: Any, target: Any) -> bool:
    if (
        not _pc(leader)
        or not _pc(target)
        or leader.location is None
        or leader.location != target.location
    ):
        return False
    try:
        from systems.combat_outcomes import InjuryState
        from systems.injury import injury_record
        from systems.visibility import target_visibility

        return (
            injury_record(leader).state is InjuryState.CONSCIOUS
            and injury_record(target).state is InjuryState.CONSCIOUS
            and target_visibility(leader, target).visible
        )
    except Exception:
        return False


def _any_fighting(*identifiers: int) -> bool:
    from systems.combat import is_fighting

    return any(
        (pc := _pc_by_id(identifier)) is not None and is_fighting(pc)
        for identifier in identifiers
    )


def _group_by_member(
    state: Mapping[str, Any], identifier: int | None
) -> tuple[str, dict[str, Any]] | None:
    return next(
        (
            (group_id, group)
            for group_id, group in state["groups"].items()
            if identifier in group["members"]
        ),
        None,
    )


def _pc_by_id(identifier: int) -> Any | None:
    try:
        from typeclasses.characters import Character

        return Character.objects.get(id=identifier)
    except Exception:
        return None


def _member_name(identifier: int, observer: Any) -> str:
    """Render a group member's display name without leaking absent records."""
    member = _pc_by_id(identifier)
    if member is None:
        return "Unknown member"
    try:
        return member.get_display_name(observer)
    except Exception:
        return "Unknown member"


def _member_class_level(member: Any) -> str:
    """Return only validated, released advancement identity."""
    if member is None:
        return "Class/level unavailable"
    try:
        from systems.advancement_info import advancement_info, display_name

        info = advancement_info(member)
        if info.valid and info.state is not None:
            return f"{display_name(info.state.class_key)} {info.state.level}"
    except Exception:
        pass
    return "Class/level unavailable"


def _member_presence(member: Any) -> str:
    """Return one bounded connection/death label with no session detail."""
    if member is None:
        return "unavailable"
    try:
        from systems.combat_outcomes import InjuryState
        from systems.injury import injury_record

        if injury_record(member).state is InjuryState.DEAD:
            return "dead"
    except Exception:
        return "unavailable"
    if member.sessions.count():
        return "connected"
    if member.attributes.get("combat_linkdead") is not None:
        return "link-dead"
    if is_character_unavailable(member):
        return "OOC"
    return "OOC"


def _member_room(member: Any, observer: Any) -> str:
    """Return a room display name only when that room authorizes the observer."""
    room = getattr(member, "location", None) if member is not None else None
    try:
        if room is not None and room.access(observer, "view"):
            return room.get_display_name(observer)
    except Exception:
        pass
    return "Location unavailable"


def _member_position(member: Any) -> str:
    """Return the canonical effective position without exposing repair detail."""
    try:
        return member.action_position.value
    except Exception:
        return "unavailable"


def _member_health(member: Any) -> str:
    """Return COMBAT-09's qualitative health band only."""
    try:
        from systems.combat_controls import health_description

        return health_description(member)
    except Exception:
        return "unavailable"


def _on_character_lifecycle(event: Any) -> None:
    """Expire offers only when a PC truly disconnects or deliberately goes OOC."""
    if event.availability is CharacterAvailability.UNAVAILABLE and event.cause in {
        UnavailabilityCause.DISCONNECT,
        UnavailabilityCause.OOC,
    }:
        cancel_invitations(event.character)


def _register_lifecycle_consumer() -> None:
    """Replace this reloadable module's callback without duplicating it."""
    consumer = LifecycleConsumer(
        GROUP_LIFECYCLE_KEY, on_character=_on_character_lifecycle
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(GROUP_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


def _pc(character: Any) -> bool:
    return (
        _id(character) is not None
        and getattr(getattr(character, "db", None), "is_player_character", None)
        is not False
    )


def _id(value: Any) -> int | None:
    identifier = getattr(value, "id", None)
    return (
        identifier
        if isinstance(identifier, int)
        and not isinstance(identifier, bool)
        and identifier > 0
        else None
    )


def _initial() -> dict[str, Any]:
    return {
        "version": GROUP_VERSION,
        "next_id": 1,
        "sequence": 0,
        "groups": {},
        "invitations": [],
    }


def _read() -> dict[str, Any]:
    raw = ServerConfig.objects.conf(GROUP_CONFIG_KEY)
    return deepcopy(raw) if _valid(raw) else _initial()


def _locked() -> dict[str, Any]:
    config, _ = ServerConfig.objects.select_for_update().get_or_create(
        db_key=GROUP_CONFIG_KEY, defaults={"db_value": _initial()}
    )
    if not _valid(config.value):
        config.db_value = _initial()
        config.save(update_fields=["db_value"])
    return deepcopy(config.value)


def _write(state: dict[str, Any]) -> None:
    ServerConfig.objects.conf(GROUP_CONFIG_KEY, value=state)


def _valid(state: Any) -> bool:
    if (
        not isinstance(state, Mapping)
        or state.get("version") != GROUP_VERSION
        or not isinstance(state.get("next_id"), int)
        or state["next_id"] < 1
        or not isinstance(state.get("sequence"), int)
        or state["sequence"] < 0
        or not isinstance(state.get("groups"), Mapping)
        or not isinstance(state.get("invitations"), Sequence)
        or isinstance(state["invitations"], (str, bytes))
    ):
        return False
    members: set[int] = set()
    for group_id, group in state["groups"].items():
        if (
            not isinstance(group_id, str)
            or not group_id.isdigit()
            or not isinstance(group, Mapping)
            or set(group) != {"leader_id", "members", "joins"}
            or not _id_value(group["leader_id"])
            or not isinstance(group["members"], Sequence)
            or isinstance(group["members"], (str, bytes))
            or not 1 <= len(group["members"]) <= GROUP_CAPACITY
            or len(set(group["members"])) != len(group["members"])
            or not all(_id_value(item) for item in group["members"])
            or group["leader_id"] not in group["members"]
            or not isinstance(group["joins"], Mapping)
            or set(group["joins"]) != {str(item) for item in group["members"]}
            or not all(
                isinstance(sequence, int) and sequence >= 0
                for sequence in group["joins"].values()
            )
        ):
            return False
        if members.intersection(group["members"]):
            return False
        members.update(group["members"])
    pairs: set[tuple[int, int]] = set()
    for entry in state["invitations"]:
        if (
            not isinstance(entry, Mapping)
            or set(entry) != {"leader_id", "target_id", "sequence"}
            or not _id_value(entry["leader_id"])
            or not _id_value(entry["target_id"])
            or entry["leader_id"] == entry["target_id"]
            or not isinstance(entry["sequence"], int)
            or entry["sequence"] < 0
            or (entry["leader_id"], entry["target_id"]) in pairs
        ):
            return False
        pairs.add((entry["leader_id"], entry["target_id"]))
    return True


def _id_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


_register_lifecycle_consumer()

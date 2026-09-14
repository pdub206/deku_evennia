"""Validated room restrictions and shared, side-effect-free admission decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

ROOM_POLICY_ATTRIBUTE = "room_policy"
ROOM_POLICY_VERSION = 1


class RoomPolicyError(ValueError):
    """A persisted or authored room-policy record is malformed."""


class AdmissionMode(str, Enum):
    """Stable movement intents understood by the admission service."""

    NORMAL = "normal"
    MOBILE = "mobile"
    FOLLOW = "follow"
    RECALL = "recall"
    FORCED = "forced"
    SPAWN = "spawn"
    RESPAWN = "respawn"
    BUILDER = "builder"


BYPASS_MODES = frozenset(
    {
        AdmissionMode.FORCED,
        AdmissionMode.SPAWN,
        AdmissionMode.RESPAWN,
        AdmissionMode.BUILDER,
    }
)


@dataclass(frozen=True)
class RoomPolicy:
    """Canonical restrictions read from one room Attribute."""

    no_combat: bool = False
    no_mobiles: bool = False
    private: bool = False
    occupant_capacity: int | None = None

    @property
    def effective_capacity(self) -> int | None:
        """Apply the private-room default without weakening a smaller limit."""
        if not self.private:
            return self.occupant_capacity
        if self.occupant_capacity is None:
            return 2
        return min(self.occupant_capacity, 2)


@dataclass(frozen=True)
class PolicyDecision:
    """A structured result safe for callers and player-facing translation."""

    allowed: bool
    reason: str = ""
    mode: AdmissionMode | None = None
    bypassed: bool = False


def default_room_policy() -> dict[str, Any]:
    """Return a new deterministic persistent record with safe defaults."""
    return {"version": ROOM_POLICY_VERSION, **asdict(RoomPolicy())}


def validate_room_policy(raw: Any) -> RoomPolicy:
    """Validate the exact, data-only room-policy schema."""
    if raw is None:
        return RoomPolicy()
    if not isinstance(raw, Mapping):
        raise RoomPolicyError("Room policy must be a mapping.")
    allowed = {"version", "no_combat", "no_mobiles", "private", "occupant_capacity"}
    if not set(raw) <= allowed:
        raise RoomPolicyError("Room policy contains unsupported fields.")
    version = raw.get("version", ROOM_POLICY_VERSION)
    if version != ROOM_POLICY_VERSION or isinstance(version, bool):
        raise RoomPolicyError("Room policy has an unsupported version.")
    values = asdict(RoomPolicy())
    values.update({key: value for key, value in raw.items() if key != "version"})
    for name in ("no_combat", "no_mobiles", "private"):
        if not isinstance(values[name], bool):
            raise RoomPolicyError(f"Room policy {name} must be true or false.")
    capacity = values["occupant_capacity"]
    if capacity is not None and (
        isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0
    ):
        raise RoomPolicyError(
            "Room policy occupant_capacity must be null or a non-negative integer."
        )
    return RoomPolicy(**values)


def room_policy(room: Any) -> RoomPolicy:
    """Read and validate a room's canonical policy without mutating it."""
    attributes = getattr(room, "attributes", None)
    if attributes is None:
        raise RoomPolicyError("Room policy owner is not a room.")
    return validate_room_policy(attributes.get(ROOM_POLICY_ATTRIBUTE))


def _policy_for_fields(room: Any, fields: set[str]) -> RoomPolicy:
    """Validate only fields capable of affecting one requested decision."""
    attributes = getattr(room, "attributes", None)
    if attributes is None:
        raise RoomPolicyError("Room policy owner is not a room.")
    raw = attributes.get(ROOM_POLICY_ATTRIBUTE)
    if raw is None:
        return RoomPolicy()
    if not isinstance(raw, Mapping):
        raise RoomPolicyError("Room policy must be a mapping.")
    allowed = {"version", "no_combat", "no_mobiles", "private", "occupant_capacity"}
    version = raw.get("version", ROOM_POLICY_VERSION)
    if (
        not set(raw) <= allowed
        or isinstance(version, bool)
        or version != ROOM_POLICY_VERSION
    ):
        raise RoomPolicyError("Room policy has an invalid shape or version.")
    relevant = {key: value for key, value in raw.items() if key in fields}
    return validate_room_policy(relevant)


def room_policy_data(room: Any) -> dict[str, Any]:
    """Return a normalized deterministic record for builder export."""
    return {"version": ROOM_POLICY_VERSION, **asdict(room_policy(room))}


def set_room_policy_value(room: Any, field: str, value: Any) -> None:
    """Validate and persist one builder-authored field atomically."""
    if field not in {"no_combat", "no_mobiles", "private", "occupant_capacity"}:
        raise RoomPolicyError("Unknown room-policy field.")
    data = room_policy_data(room)
    data[field] = value
    policy = validate_room_policy(data)
    room.attributes.add(
        ROOM_POLICY_ATTRIBUTE, {"version": ROOM_POLICY_VERSION, **asdict(policy)}
    )


def admission_decision(
    actor: Any, destination: Any, *, mode: AdmissionMode | str = AdmissionMode.NORMAL
) -> PolicyDecision:
    """Decide admission from current contents, re-callable immediately before move."""
    try:
        mode = AdmissionMode(mode)
    except (TypeError, ValueError):
        return PolicyDecision(False, "invalid_mode")
    bypassed = mode in BYPASS_MODES
    try:
        policy = _policy_for_fields(
            destination, {"no_mobiles", "private", "occupant_capacity"}
        )
    except RoomPolicyError:
        return PolicyDecision(False, "malformed_policy", mode)
    if bypassed:
        return PolicyDecision(True, mode=mode, bypassed=True)
    is_npc = getattr(getattr(actor, "db", None), "is_player_character", None) is False
    if is_npc and policy.no_mobiles:
        return PolicyDecision(False, "no_mobiles", mode)
    capacity = policy.effective_capacity
    if capacity is not None:
        occupants = sum(
            1
            for obj in destination.contents_get(content_type="character")
            if obj is not actor
        )
        if occupants >= capacity:
            return PolicyDecision(False, "room_full", mode)
    return PolicyDecision(True, mode=mode)


def combat_decision(room: Any) -> PolicyDecision:
    """Decide whether a new hostile action may occur in this room."""
    try:
        policy = _policy_for_fields(room, {"no_combat"})
    except RoomPolicyError:
        return PolicyDecision(False, "malformed_policy")
    return PolicyDecision(not policy.no_combat, "no_combat" if policy.no_combat else "")


def remote_view_decision(room: Any) -> PolicyDecision:
    """Decide whether adjacent-room details may be inspected remotely."""
    try:
        policy = _policy_for_fields(room, {"private"})
    except RoomPolicyError:
        return PolicyDecision(False, "malformed_policy")
    return PolicyDecision(not policy.private, "private" if policy.private else "")

"""Canonical INTERACT-01A door configuration and synchronized live state.

Evennia keeps each direction as a separate Exit object.  This module gives a
door pair one validated logical state while retaining those ordinary exits for
commands, locks, traversal hooks, and area export.  Persisted records contain
only primitives; pair resolution is derived from a stable key and reciprocal
room topology rather than a live object reference.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any

from django.db import transaction
from evennia.objects.models import ObjectDB
from evennia.utils.utils import inherits_from

DOOR_STATE_ATTRIBUTE = "door_state"
DOOR_STATE_VERSION = 1
MAX_DOOR_DC = 30
MAX_IDENTITY_LENGTH = 128

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_INITIAL_STATES = frozenset({"open", "closed", "locked"})
_STATE_KEYS = frozenset(
    {
        "version",
        "is_door",
        "open",
        "locked",
        "key_kind",
        "pickable",
        "pick_dc",
        "hidden",
        "discovery_dc",
        "pair_key",
        "initial_state",
        "last_reset_id",
        "last_state_id",
    }
)
_AREA_KEYS = frozenset(
    {
        "version",
        "door",
        "initial_state",
        "key_kind",
        "pickable",
        "pick_dc",
        "hidden",
        "discovery_dc",
        "pair_key",
    }
)


class DoorError(ValueError):
    """Raised when an exit's door data cannot be used safely."""


@dataclass(frozen=True)
class DoorState:
    """One validated persistent door record."""

    version: int = DOOR_STATE_VERSION
    is_door: bool = True
    open: bool = False
    locked: bool = False
    key_kind: str | None = None
    pickable: bool = False
    pick_dc: int | None = None
    hidden: bool = False
    discovery_dc: int | None = None
    pair_key: str | None = None
    initial_state: str = "closed"
    last_reset_id: str | None = None
    last_state_id: str | None = None


@dataclass(frozen=True)
class DoorDecision:
    """A side-effect-free traversal decision with a player-safe reason."""

    allowed: bool
    reason: str = ""
    message: str = ""


@dataclass(frozen=True)
class DoorMutation:
    """The result of one idempotent state or reset mutation."""

    status: str
    state: DoorState
    peer_id: int | None = None


def door_state(exit_obj: Any) -> DoorState | None:
    """Return validated state for a door/container, or ``None`` for a passage."""
    _require_target(exit_obj)
    raw = exit_obj.attributes.get(DOOR_STATE_ATTRIBUTE)
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != _STATE_KEYS:
        raise DoorError("Door state has an invalid schema.")
    state = DoorState(**dict(raw))
    _validate_state(state)
    return state


def configure_door(
    exit_obj: Any, *, door: bool = True, **changes: Any
) -> DoorState | None:
    """Apply validated authored configuration, synchronizing a present peer.

    Setting the first side's pair key is allowed before its reciprocal exit is
    configured.  Traversal still fails closed until the peer exists.  Once a
    reciprocal peer is present, subsequent configuration updates both sides.
    """
    _require_target(exit_obj)
    if not isinstance(door, bool):
        raise DoorError("Door must be configured on or off.")
    current = door_state(exit_obj)
    old_peer = _optional_peer(exit_obj, current.pair_key if current else None)

    if not door:
        if changes:
            raise DoorError("A passage cannot retain door-only fields.")
        targets = (exit_obj, old_peer) if old_peer is not None else (exit_obj,)
        _write_many({target: None for target in targets})
        return None

    state = current or DoorState()
    unknown = set(changes) - {
        "initial_state",
        "key_kind",
        "pickable",
        "pick_dc",
        "hidden",
        "discovery_dc",
        "pair_key",
    }
    if unknown:
        raise DoorError(f"Unknown door field(s): {', '.join(sorted(unknown))}.")

    normalized = _apply_configuration(state, changes)
    if (
        not inherits_from(exit_obj, "evennia.objects.objects.DefaultExit")
        and normalized.pair_key
    ):
        raise DoorError("Containers cannot have synchronized door pairs.")
    if (
        current
        and current.pair_key
        and changes.get("pair_key", current.pair_key)
        not in {
            current.pair_key,
            None,
        }
    ):
        if old_peer is not None:
            raise DoorError(
                "Clear an existing synchronized pair before assigning another."
            )

    if normalized.pair_key is None:
        targets = (exit_obj, old_peer) if old_peer is not None else (exit_obj,)
        values = {target: replace(normalized, pair_key=None) for target in targets}
        _write_many(values)
        return normalized

    new_peer = _optional_peer(exit_obj, normalized.pair_key)
    targets = (exit_obj, new_peer) if new_peer is not None else (exit_obj,)
    _write_many({target: normalized for target in targets})
    return normalized


def configure_door_field(exit_obj: Any, name: str, value: Any) -> DoorState | None:
    """Apply one builder-facing door field through the canonical validator."""
    if name == "door":
        if value not in {"on", "off"}:
            raise DoorError("Door must be on or off.")
        return configure_door(exit_obj, door=value == "on")
    state = door_state(exit_obj)
    if state is None:
        raise DoorError("Set door on before configuring door fields.")
    if name in {"pickable", "hidden"}:
        if value not in {"on", "off"}:
            raise DoorError(f"{name.replace('_', ' ').title()} must be on or off.")
        value = value == "on"
    return configure_door(exit_obj, **{name: value})


def configured_door_record(raw: Any, name: str, value: Any) -> dict[str, Any] | None:
    """Apply one builder field to a primitive prototype door record."""
    if raw is None:
        state = None
    elif not isinstance(raw, Mapping) or set(raw) != _STATE_KEYS:
        raise DoorError("Door state has an invalid schema.")
    else:
        state = DoorState(**dict(raw))
        _validate_state(state)
    if name == "door":
        if value not in {"on", "off"}:
            raise DoorError("Door must be on or off.")
        return asdict(state or DoorState()) if value == "on" else None
    if state is None:
        raise DoorError("Set door on before configuring door fields.")
    if name in {"pickable", "hidden"} and value in {"on", "off"}:
        value = value == "on"
    return asdict(_apply_configuration(state, {name: value}))


def transition_door(
    exit_obj: Any,
    *,
    open: bool,
    locked: bool,
    state_id: str,
) -> DoorMutation:
    """Atomically set the live logical state of one door or synchronized pair."""
    state = _require_door(exit_obj)
    identity = _validate_identity(state_id, "state identity")
    peer = paired_exit(exit_obj) if state.pair_key else None
    targets = (exit_obj, peer) if peer is not None else (exit_obj,)
    states = tuple(_require_door(target) for target in targets)

    duplicate = [item for item in states if item.last_state_id == identity]
    if duplicate:
        if len(duplicate) != len(states) or any(
            item.open != open or item.locked != locked for item in states
        ):
            raise DoorError("State identity conflicts with an earlier transition.")
        return DoorMutation("duplicate", states[0], getattr(peer, "id", None))

    updated = replace(state, open=open, locked=locked, last_state_id=identity)
    _validate_state(updated)
    _write_many({target: updated for target in targets})
    return DoorMutation(
        "changed" if (state.open, state.locked) != (open, locked) else "unchanged",
        updated,
        getattr(peer, "id", None),
    )


def restore_initial_state(exit_obj: Any, reset_id: str) -> DoorMutation:
    """Restore authored live state once for a stable reset identity."""
    state = _require_door(exit_obj)
    identity = _validate_identity(reset_id, "reset identity")
    peer = paired_exit(exit_obj) if state.pair_key else None
    targets = (exit_obj, peer) if peer is not None else (exit_obj,)
    states = tuple(_require_door(target) for target in targets)
    if any(item.last_reset_id == identity for item in states):
        if not all(item.last_reset_id == identity for item in states):
            raise DoorError("Reset identity is only partially recorded on a door pair.")
        return DoorMutation("duplicate", states[0], getattr(peer, "id", None))

    target_open = state.initial_state == "open"
    target_locked = state.initial_state == "locked"
    updated = replace(
        state,
        open=target_open,
        locked=target_locked,
        last_reset_id=identity,
    )
    _write_many({target: updated for target in targets})
    return DoorMutation("restored", updated, getattr(peer, "id", None))


def paired_exit(exit_obj: Any) -> Any:
    """Return the sole reciprocal pair and reject missing or divergent state."""
    _require_exit(exit_obj)
    state = _require_door(exit_obj)
    if state.pair_key is None:
        raise DoorError("This door has no synchronized pair.")
    peer = _required_peer(exit_obj, state.pair_key)
    peer_state = _require_door(peer)
    if peer_state.pair_key != state.pair_key or _logical_state(
        peer_state
    ) != _logical_state(state):
        raise DoorError("Synchronized door sides disagree.")
    return peer


def traversal_decision(exit_obj: Any) -> DoorDecision:
    """Return whether ordinary traversal may use this passage or door."""
    try:
        state = door_state(exit_obj)
        if state is None:
            return DoorDecision(True)
        if state.pair_key:
            paired_exit(exit_obj)
        if state.hidden:
            return DoorDecision(False, "hidden", "You cannot go that way.")
        if not state.open:
            return DoorDecision(False, "closed", "The way is closed.")
        return DoorDecision(True)
    except DoorError:
        return DoorDecision(
            False,
            "invalid_door",
            "That way is unavailable. Please report it to staff.",
        )


def door_area_data(exit_obj: Any) -> dict[str, Any] | None:
    """Return deterministic authored configuration for tracked area data."""
    state = door_state(exit_obj)
    if state is None:
        return None
    if state.pair_key:
        paired_exit(exit_obj)
    return {
        "version": DOOR_STATE_VERSION,
        "door": True,
        "initial_state": state.initial_state,
        "key_kind": state.key_kind,
        "pickable": state.pickable,
        "pick_dc": state.pick_dc,
        "hidden": state.hidden,
        "discovery_dc": state.discovery_dc,
        "pair_key": state.pair_key,
    }


def apply_door_area_data(exit_obj: Any, data: Any) -> DoorState | None:
    """Install validated authored area data with fresh live/reset identities."""
    if data is None:
        return configure_door(exit_obj, door=False)
    validated = validate_door_area_data(data)
    return configure_door(
        exit_obj,
        initial_state=validated["initial_state"],
        key_kind=validated["key_kind"],
        pickable=validated["pickable"],
        pick_dc=validated["pick_dc"],
        hidden=validated["hidden"],
        discovery_dc=validated["discovery_dc"],
        pair_key=validated["pair_key"],
    )


def validate_door_area_data(data: Any) -> dict[str, Any]:
    """Return canonical authored door data without requiring a live Exit."""
    if not isinstance(data, Mapping) or set(data) != _AREA_KEYS:
        raise DoorError("Area door data has an invalid schema.")
    if data.get("version") != DOOR_STATE_VERSION or data.get("door") is not True:
        raise DoorError("Area door data has an unsupported version or kind.")
    state = _apply_configuration(
        DoorState(),
        {
            "initial_state": data["initial_state"],
            "key_kind": data["key_kind"],
            "pickable": data["pickable"],
            "pick_dc": data["pick_dc"],
            "hidden": data["hidden"],
            "discovery_dc": data["discovery_dc"],
            "pair_key": data["pair_key"],
        },
    )
    return {
        "version": DOOR_STATE_VERSION,
        "door": True,
        "initial_state": state.initial_state,
        "key_kind": state.key_kind,
        "pickable": state.pickable,
        "pick_dc": state.pick_dc,
        "hidden": state.hidden,
        "discovery_dc": state.discovery_dc,
        "pair_key": state.pair_key,
    }


def validate_area_exit_doors(exits: Any) -> None:
    """Reject malformed or incomplete synchronized pairs before area mutation."""
    if not isinstance(exits, (list, tuple)):
        raise DoorError("Area exits must be a list or tuple.")
    paired: dict[str, list[tuple[Any, Any, dict[str, Any]]]] = {}
    for entry in exits:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            raise DoorError(
                "Each area exit must contain source, name, destination, and data."
            )
        source, _direction, destination, attrs = entry
        if not isinstance(attrs, Mapping):
            raise DoorError("Area exit data must be a mapping.")
        raw = attrs.get("door_state")
        if raw is None:
            continue
        config = validate_door_area_data(raw)
        if config["pair_key"]:
            paired.setdefault(config["pair_key"], []).append(
                (source, destination, config)
            )
    for pair_key, entries in paired.items():
        if len(entries) != 2:
            raise DoorError(f"Door pair '{pair_key}' must contain exactly two sides.")
        first, second = entries
        if first[0] != second[1] or first[1] != second[0]:
            raise DoorError(f"Door pair '{pair_key}' is not reciprocal.")
        if first[2] != second[2]:
            raise DoorError(f"Door pair '{pair_key}' has divergent authored state.")


def door_diagnostic(exit_obj: Any) -> dict[str, Any]:
    """Return a bounded primitive diagnostic without mutating malformed data."""
    try:
        state = door_state(exit_obj)
        if state is None:
            return {"status": "passage", "exit_id": exit_obj.id}
        peer_id = None
        status = "door"
        if state.pair_key:
            peer_id = paired_exit(exit_obj).id
            status = "paired"
        return {
            "status": status,
            "exit_id": exit_obj.id,
            "peer_id": peer_id,
            "state": asdict(state),
        }
    except DoorError as err:
        return {
            "status": "invalid",
            "exit_id": getattr(exit_obj, "id", None),
            "reason": str(err),
        }


def door_field_value(exit_obj: Any, name: str) -> Any:
    """Return one builder field, preserving a visible invalid diagnostic."""
    try:
        state = door_state(exit_obj)
    except DoorError:
        return "(invalid door state)"
    if name == "door":
        return "on" if state is not None else "off"
    if state is None:
        return None
    return getattr(state, name)


def _apply_configuration(state: DoorState, changes: Mapping[str, Any]) -> DoorState:
    """Normalize authored changes and reset live state when initial state changes."""
    values = dict(changes)
    for name in ("key_kind", "pair_key"):
        if name in values:
            values[name] = _validate_key(values[name], name.replace("_", " "))
    for name in ("pickable", "hidden"):
        if name in values and not isinstance(values[name], bool):
            raise DoorError(f"{name.replace('_', ' ').title()} must be true or false.")
    for name in ("pick_dc", "discovery_dc"):
        if name in values:
            values[name] = _validate_dc(values[name], name.replace("_", " "))
    if "initial_state" in values:
        initial = str(values["initial_state"]).strip().lower()
        if initial not in _INITIAL_STATES:
            raise DoorError("Initial state must be open, closed, or locked.")
        values["initial_state"] = initial
        values["open"] = initial == "open"
        values["locked"] = initial == "locked"

    updated = replace(state, **values)
    if updated.pickable and updated.pick_dc is None:
        updated = replace(updated, pick_dc=10)
    if not updated.pickable:
        updated = replace(updated, pick_dc=None)
    if updated.hidden and updated.discovery_dc is None:
        updated = replace(updated, discovery_dc=10)
    if not updated.hidden:
        updated = replace(updated, discovery_dc=None)
    _validate_state(updated)
    return updated


def _validate_state(state: DoorState) -> None:
    """Validate every persisted invariant exactly."""
    if state.version != DOOR_STATE_VERSION or state.is_door is not True:
        raise DoorError("Door state has an unsupported version or kind.")
    for name in ("open", "locked", "pickable", "hidden"):
        if not isinstance(getattr(state, name), bool):
            raise DoorError(f"Door field '{name}' must be true or false.")
    if state.open and state.locked:
        raise DoorError("An open door cannot be locked.")
    if state.initial_state not in _INITIAL_STATES:
        raise DoorError("Door initial state is invalid.")
    _validate_key(state.key_kind, "key kind")
    _validate_key(state.pair_key, "pair key")
    _validate_dc(state.pick_dc, "pick DC")
    _validate_dc(state.discovery_dc, "discovery DC")
    if state.pickable != (state.pick_dc is not None):
        raise DoorError("Pickable state and pick DC disagree.")
    if state.hidden != (state.discovery_dc is not None):
        raise DoorError("Hidden state and discovery DC disagree.")
    for value, label in (
        (state.last_reset_id, "reset identity"),
        (state.last_state_id, "state identity"),
    ):
        if value is not None:
            _validate_identity(value, label)


def _validate_key(value: Any, label: str) -> str | None:
    """Validate a stable data key or explicit absence."""
    if value in (None, "", "none"):
        return None
    if not isinstance(value, str):
        raise DoorError(f"Door {label} must be a string or none.")
    normalized = value.strip().lower()
    if len(normalized) > MAX_IDENTITY_LENGTH or not _KEY_RE.fullmatch(normalized):
        raise DoorError(f"Door {label} is not a valid stable key.")
    return normalized


def _validate_dc(value: Any, label: str) -> int | None:
    """Validate an optional bounded difficulty class."""
    if value in (None, "", "none"):
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_DOOR_DC
    ):
        raise DoorError(f"Door {label} must be between 0 and {MAX_DOOR_DC}, or none.")
    return value


def _validate_identity(value: Any, label: str) -> str:
    """Validate a bounded idempotency identity without constraining separators."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_IDENTITY_LENGTH
    ):
        raise DoorError(f"Door {label} must be a bounded non-empty string.")
    return value


def _require_exit(exit_obj: Any) -> None:
    """Reject non-exits before touching arbitrary Attribute storage."""
    if not inherits_from(exit_obj, "evennia.objects.objects.DefaultExit"):
        raise DoorError("Door state requires an Exit object.")


def _require_target(target: Any) -> None:
    """Accept an Exit or a data-driven openable container."""
    if inherits_from(target, "evennia.objects.objects.DefaultExit"):
        return
    if str(target.attributes.get("type") or "").casefold() == "container":
        return
    raise DoorError("Door state requires an Exit or container object.")


def _require_door(exit_obj: Any) -> DoorState:
    """Return a door or raise for a passage."""
    state = door_state(exit_obj)
    if state is None:
        raise DoorError("This exit is not a door.")
    return state


def _logical_state(state: DoorState) -> tuple[Any, ...]:
    """Return fields which both sides of one logical door must share."""
    return (
        state.open,
        state.locked,
        state.key_kind,
        state.pickable,
        state.pick_dc,
        state.hidden,
        state.discovery_dc,
        state.initial_state,
        state.pair_key,
        state.last_reset_id,
        state.last_state_id,
    )


def _raw_pair_key(exit_obj: Any) -> str | None:
    """Read only enough raw data to locate a possibly malformed peer."""
    raw = exit_obj.attributes.get(DOOR_STATE_ATTRIBUTE)
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("pair_key")
    try:
        return _validate_key(value, "pair key")
    except DoorError:
        return None


def _peer_candidates(exit_obj: Any, pair_key: str) -> tuple[Any, ...]:
    """Find reciprocal exits bearing one stable logical-pair key."""
    source, destination = exit_obj.location, exit_obj.destination
    if source is None or destination is None:
        return ()
    candidates = ObjectDB.objects.filter(
        db_location_id=destination.id,
        db_destination_id=source.id,
    )
    return tuple(
        candidate
        for candidate in candidates
        if candidate.id != exit_obj.id and _raw_pair_key(candidate) == pair_key
    )


def _required_peer(exit_obj: Any, pair_key: str) -> Any:
    """Require exactly one reciprocal candidate."""
    candidates = _peer_candidates(exit_obj, pair_key)
    if not candidates:
        raise DoorError("Synchronized door peer is missing.")
    if len(candidates) > 1:
        raise DoorError("Synchronized door peer is ambiguous.")
    return candidates[0]


def _optional_peer(exit_obj: Any, pair_key: str | None) -> Any | None:
    """Return a reciprocal candidate when present, rejecting ambiguity."""
    if pair_key is None:
        return None
    candidates = _peer_candidates(exit_obj, pair_key)
    if len(candidates) > 1:
        raise DoorError("Synchronized door peer is ambiguous.")
    return candidates[0] if candidates else None


def _write_many(states: Mapping[Any, DoorState | None]) -> None:
    """Write one or two synchronized records in one database transaction."""
    targets = tuple(states)
    identifiers = sorted(target.id for target in targets)
    previous = {
        target: deepcopy(target.attributes.get(DOOR_STATE_ATTRIBUTE))
        for target in targets
    }
    write_started = False
    try:
        with transaction.atomic():
            locked = ObjectDB.objects.select_for_update().filter(id__in=identifiers)
            if locked.count() != len(identifiers):
                raise DoorError("A door changed while it was being updated.")

            # The mutation was planned before these locks became available.
            # Reject a stale plan instead of overwriting an earlier waiter.
            for target, expected in previous.items():
                target.attributes.reset_cache()
                current = target.attributes.get(DOOR_STATE_ATTRIBUTE)
                if current != expected:
                    raise DoorError("A door changed while it was being updated.")

            for target, state in states.items():
                write_started = True
                _write_state(target, state)
    except Exception:
        if write_started:
            # Django rolls storage back, but Evennia's Attribute cache has
            # already observed earlier writes. Repair it so live traversal
            # cannot see half a logical door.
            for target, raw in previous.items():
                if raw is None:
                    target.attributes.remove(DOOR_STATE_ATTRIBUTE)
                else:
                    target.attributes.add(DOOR_STATE_ATTRIBUTE, raw)
        else:
            for target in targets:
                target.attributes.reset_cache()
        raise


def _write_state(target: Any, state: DoorState | None) -> None:
    """Write one record; kept narrow so rollback behavior is fault-injectable."""
    if state is None:
        target.attributes.remove(DOOR_STATE_ATTRIBUTE)
    else:
        _validate_state(state)
        target.attributes.add(DOOR_STATE_ATTRIBUTE, asdict(state))

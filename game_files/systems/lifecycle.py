"""Idempotent character-availability and server-transition infrastructure.

WORLD-03 owns these boundaries so later combat, following, queued-action,
corpse, and mobile systems can react to lifecycle changes without depending on
Evennia Session objects or inventing their own reload bookkeeping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from evennia.utils import logger

CHARACTER_LIFECYCLE_ATTRIBUTE = "world_lifecycle"
CHARACTER_NOTICE_ATTRIBUTE = "pending_world_notices"
SERVER_LIFECYCLE_CONFIG_KEY = "world_lifecycle_state"
LIFECYCLE_SCHEMA_VERSION = 1
MAX_PENDING_NOTICES = 100

_SESSION_CAUSE_ATTRIBUTE = "_world_unpuppet_cause"
_SERVER_BOOT_ID = uuid4().hex
_KEY_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")


class LifecycleError(ValueError):
    """A lifecycle definition or operation violates the shared contract."""


class CharacterAvailability(str, Enum):
    """Whether a character currently has a controlling session."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class UnavailabilityCause(str, Enum):
    """Why the final controlling session left a character."""

    DISCONNECT = "disconnect"
    OOC = "ooc"
    UNPUPPET = "unpuppet"
    COLD_SHUTDOWN = "cold_shutdown"


class ServerTransitionMode(str, Enum):
    """Server transitions with different persistence policies."""

    HOT_RELOAD = "hot_reload"
    COLD_RESTART = "cold_restart"


class ServerTransitionPhase(str, Enum):
    """Whether consumers should prepare state or recover it."""

    PREPARE = "prepare"
    RECOVER = "recover"


@dataclass(frozen=True)
class CharacterLifecycleEvent:
    """One persistent character-availability transition."""

    character: Any
    availability: CharacterAvailability
    sequence: int
    cause: UnavailabilityCause | None = None


@dataclass(frozen=True)
class ServerLifecycleEvent:
    """One persistent server transition with a stable idempotency token."""

    mode: ServerTransitionMode
    phase: ServerTransitionPhase
    sequence: int


CharacterCallback = Callable[[CharacterLifecycleEvent], None]
ServerCallback = Callable[[ServerLifecycleEvent], None]


@dataclass(frozen=True)
class LifecycleConsumer:
    """Callbacks supplied by one live-world subsystem."""

    key: str
    on_character: CharacterCallback | None = None
    on_server: ServerCallback | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise LifecycleError("A lifecycle consumer key must be text.")
        normalized = self.key.strip().lower()
        if not normalized or any(char not in _KEY_CHARACTERS for char in normalized):
            raise LifecycleError("A lifecycle consumer needs a stable lowercase key.")
        if self.on_character is not None and not callable(self.on_character):
            raise LifecycleError("A character lifecycle callback must be callable.")
        if self.on_server is not None and not callable(self.on_server):
            raise LifecycleError("A server lifecycle callback must be callable.")
        object.__setattr__(self, "key", normalized)


@dataclass(frozen=True)
class LifecycleResult:
    """Summary of one transition and its isolated consumer failures."""

    changed: bool
    event: CharacterLifecycleEvent | ServerLifecycleEvent | None
    dispatched: int = 0
    failures: int = 0


@dataclass(frozen=True)
class NoticeDeliveryResult:
    """Summary of reconnect notices removed and attempted exactly once."""

    delivered: int
    failures: int


_CONSUMERS: dict[str, LifecycleConsumer] = {}


def register_lifecycle_consumer(consumer: LifecycleConsumer) -> None:
    """Register one code-defined consumer under its stable unique key."""
    if not isinstance(consumer, LifecycleConsumer):
        raise LifecycleError("Only LifecycleConsumer instances may be registered.")
    if consumer.key in _CONSUMERS:
        raise LifecycleError(
            f"Lifecycle consumer '{consumer.key}' is already registered."
        )
    _CONSUMERS[consumer.key] = consumer


def unregister_lifecycle_consumer(key: str) -> None:
    """Remove a consumer, primarily for tests and optional systems."""
    _CONSUMERS.pop(str(key).strip().lower(), None)


def mark_session_unpuppet_cause(session: Any, cause: UnavailabilityCause) -> None:
    """Mark a non-persistent Session with its impending unpuppet cause."""
    if session is None or not isinstance(cause, UnavailabilityCause):
        raise LifecycleError("A session cause requires a session and cause enum.")
    setattr(session.ndb, _SESSION_CAUSE_ATTRIBUTE, cause.value)


def clear_session_unpuppet_cause(session: Any) -> None:
    """Clear a temporary Session cause after the unpuppet operation."""
    if session is not None:
        delattr(session.ndb, _SESSION_CAUSE_ATTRIBUTE)


def resolve_unavailability_cause(
    session: Any = None, *, reason: Any = None, cold_shutdown: bool = False
) -> UnavailabilityCause:
    """Resolve a session-free cause for a character lifecycle event."""
    if cold_shutdown:
        return UnavailabilityCause.COLD_SHUTDOWN
    if reason:
        return UnavailabilityCause.DISCONNECT
    if session is not None:
        value = getattr(session.ndb, _SESSION_CAUSE_ATTRIBUTE, None)
        try:
            return UnavailabilityCause(value)
        except (TypeError, ValueError):
            pass
    return UnavailabilityCause.UNPUPPET


def mark_character_unavailable(
    character: Any,
    cause: UnavailabilityCause,
    *,
    has_controlling_sessions: bool,
) -> LifecycleResult:
    """Persist and dispatch the final-session transition exactly once."""
    if not isinstance(cause, UnavailabilityCause):
        raise LifecycleError("Character unavailability requires a cause enum.")
    if has_controlling_sessions:
        return LifecycleResult(False, None)
    return _transition_character(
        character,
        CharacterAvailability.UNAVAILABLE,
        cause=cause,
    )


def mark_character_available(character: Any) -> LifecycleResult:
    """Persist and dispatch the first-session reconnect transition once."""
    return _transition_character(character, CharacterAvailability.AVAILABLE)


def is_character_unavailable(character: Any) -> bool:
    """Return whether valid persistent lifecycle state marks a character away."""
    state = character.attributes.get(CHARACTER_LIFECYCLE_ATTRIBUTE)
    return (
        isinstance(state, Mapping)
        and state.get("version") == LIFECYCLE_SCHEMA_VERSION
        and state.get("availability") == CharacterAvailability.UNAVAILABLE.value
    )


def queue_or_deliver_character_notice(
    character: Any, message: str, *, notice_id: str
) -> bool:
    """Queue an unavailable character's notice, or deliver it immediately.

    Returns ``True`` when queued and ``False`` when delivered immediately.
    Duplicate persistent notice IDs are ignored.
    """
    if not isinstance(message, str) or not message:
        raise LifecycleError("A reconnect notice needs non-empty text.")
    if not isinstance(notice_id, str) or not notice_id:
        raise LifecycleError("A reconnect notice needs a stable identity.")
    if not is_character_unavailable(character):
        character.msg(message)
        return False

    entries = _read_notice_entries(character)
    if any(entry["id"] == notice_id for entry in entries):
        return True
    entries.append({"id": notice_id, "message": message})
    if len(entries) > MAX_PENDING_NOTICES:
        entries = entries[-MAX_PENDING_NOTICES:]
        logger.log_warn(
            f"Reconnect notices exceeded the limit for object #{character.id}; "
            "the oldest notice was discarded."
        )
    _write_notice_entries(character, entries)
    return True


def deliver_character_notices(character: Any) -> NoticeDeliveryResult:
    """Remove pending notices first, then attempt each delivery at most once."""
    entries = _read_notice_entries(character)
    if not entries:
        return NoticeDeliveryResult(0, 0)
    _write_notice_entries(character, [])

    delivered = 0
    failures = 0
    for entry in entries:
        try:
            character.msg(entry["message"])
        except Exception:
            failures += 1
            logger.log_trace(
                f"Reconnect notice '{entry['id']}' failed for object "
                f"#{character.id}."
            )
        else:
            delivered += 1
    return NoticeDeliveryResult(delivered, failures)


def prepare_server_transition(
    mode: ServerTransitionMode, *, boot_id: str = _SERVER_BOOT_ID
) -> LifecycleResult:
    """Persist and dispatch one reload/shutdown preparation token."""
    if not isinstance(mode, ServerTransitionMode):
        raise LifecycleError("A server transition requires a mode enum.")
    state = _read_server_state()
    pending = state["pending"]
    if (
        pending is not None
        and pending["mode"] == mode.value
        and pending["boot_id"] == boot_id
    ):
        return LifecycleResult(False, None)

    sequence = state["sequence"] + 1
    state["sequence"] = sequence
    state["pending"] = {
        "mode": mode.value,
        "sequence": sequence,
        "boot_id": boot_id,
    }
    _write_server_state(state)
    event = ServerLifecycleEvent(mode, ServerTransitionPhase.PREPARE, sequence)
    dispatched, failures = _dispatch_server(event)
    return LifecycleResult(True, event, dispatched, failures)


def recover_server_transition(
    mode: ServerTransitionMode, *, boot_id: str = _SERVER_BOOT_ID
) -> LifecycleResult:
    """Persist and dispatch one reload/start recovery token."""
    if not isinstance(mode, ServerTransitionMode):
        raise LifecycleError("A server transition requires a mode enum.")
    state = _read_server_state()
    recovered = state["last_recovery"]
    if (
        recovered is not None
        and recovered["mode"] == mode.value
        and recovered["boot_id"] == boot_id
    ):
        return LifecycleResult(False, None)

    pending = state["pending"]
    if pending is not None and pending["mode"] == mode.value:
        sequence = pending["sequence"]
    else:
        sequence = state["sequence"] + 1
        state["sequence"] = sequence
    state["pending"] = None
    state["last_recovery"] = {
        "mode": mode.value,
        "sequence": sequence,
        "boot_id": boot_id,
    }
    _write_server_state(state)
    event = ServerLifecycleEvent(mode, ServerTransitionPhase.RECOVER, sequence)
    dispatched, failures = _dispatch_server(event)
    return LifecycleResult(True, event, dispatched, failures)


def _transition_character(
    character: Any,
    availability: CharacterAvailability,
    *,
    cause: UnavailabilityCause | None = None,
) -> LifecycleResult:
    """Persist one character transition before dispatching consumers."""
    state = _read_character_state(character, default=availability)
    if state["availability"] == availability.value:
        return LifecycleResult(False, None)

    sequence = state["sequence"] + 1
    character.attributes.add(
        CHARACTER_LIFECYCLE_ATTRIBUTE,
        {
            "version": LIFECYCLE_SCHEMA_VERSION,
            "availability": availability.value,
            "sequence": sequence,
            "cause": cause.value if cause is not None else None,
        },
    )
    event = CharacterLifecycleEvent(character, availability, sequence, cause)
    dispatched, failures = _dispatch_character(event)
    return LifecycleResult(True, event, dispatched, failures)


def _read_character_state(
    character: Any, *, default: CharacterAvailability
) -> dict[str, Any]:
    """Return validated character state or a safe opposite-state default."""
    state = character.attributes.get(CHARACTER_LIFECYCLE_ATTRIBUTE)
    if state is None:
        opposite = (
            CharacterAvailability.UNAVAILABLE
            if default is CharacterAvailability.AVAILABLE
            else CharacterAvailability.AVAILABLE
        )
        return {
            "version": LIFECYCLE_SCHEMA_VERSION,
            "availability": opposite.value,
            "sequence": 0,
            "cause": None,
        }
    if (
        isinstance(state, Mapping)
        and state.get("version") == LIFECYCLE_SCHEMA_VERSION
        and state.get("availability")
        in {value.value for value in CharacterAvailability}
        and state.get("cause")
        in {None, *(value.value for value in UnavailabilityCause)}
        and isinstance(state.get("sequence"), int)
        and not isinstance(state.get("sequence"), bool)
        and state["sequence"] >= 0
    ):
        return dict(state)
    logger.log_err(
        f"Malformed character lifecycle state on object #{character.id}; "
        "reinitializing it safely."
    )
    opposite = (
        CharacterAvailability.UNAVAILABLE
        if default is CharacterAvailability.AVAILABLE
        else CharacterAvailability.AVAILABLE
    )
    return {
        "version": LIFECYCLE_SCHEMA_VERSION,
        "availability": opposite.value,
        "sequence": 0,
        "cause": None,
    }


def _read_notice_entries(character: Any) -> list[dict[str, str]]:
    """Return validated notice records, resetting malformed storage safely."""
    state = character.attributes.get(CHARACTER_NOTICE_ATTRIBUTE)
    if state is None:
        return []
    if isinstance(state, Mapping) and state.get("version") == LIFECYCLE_SCHEMA_VERSION:
        entries = state.get("entries")
        if (
            isinstance(entries, Sequence)
            and not isinstance(entries, (str, bytes))
            and len(entries) <= MAX_PENDING_NOTICES
            and all(
                isinstance(entry, Mapping)
                and isinstance(entry.get("id"), str)
                and bool(entry["id"])
                and isinstance(entry.get("message"), str)
                and bool(entry["message"])
                for entry in entries
            )
        ):
            return [dict(entry) for entry in entries]
    logger.log_err(
        f"Malformed reconnect notice state on object #{character.id}; "
        "resetting it safely."
    )
    _write_notice_entries(character, [])
    return []


def _write_notice_entries(character: Any, entries: list[dict[str, str]]) -> None:
    """Persist detached primitive notice records."""
    character.attributes.add(
        CHARACTER_NOTICE_ATTRIBUTE,
        {"version": LIFECYCLE_SCHEMA_VERSION, "entries": list(entries)},
    )


def _initial_server_state() -> dict[str, Any]:
    """Return empty serializable server lifecycle state."""
    return {
        "version": LIFECYCLE_SCHEMA_VERSION,
        "sequence": 0,
        "pending": None,
        "last_recovery": None,
    }


def _read_server_state() -> dict[str, Any]:
    """Return validated persistent server state, repairing malformed data."""
    from evennia.server.models import ServerConfig

    state = ServerConfig.objects.conf(SERVER_LIFECYCLE_CONFIG_KEY)
    if state is None:
        return _initial_server_state()
    if not _valid_server_state(state):
        logger.log_err("Malformed persistent server lifecycle state; resetting it.")
        return _initial_server_state()
    return {
        "version": state["version"],
        "sequence": state["sequence"],
        "pending": dict(state["pending"]) if state["pending"] else None,
        "last_recovery": (
            dict(state["last_recovery"]) if state["last_recovery"] else None
        ),
    }


def _write_server_state(state: dict[str, Any]) -> None:
    """Persist server transition state before consumers receive its token."""
    from evennia.server.models import ServerConfig

    ServerConfig.objects.conf(SERVER_LIFECYCLE_CONFIG_KEY, value=state)


def _valid_server_state(state: Any) -> bool:
    """Return whether server transition state has the supported primitive shape."""
    if (
        not isinstance(state, Mapping)
        or state.get("version") != LIFECYCLE_SCHEMA_VERSION
    ):
        return False
    sequence = state.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        return False
    records_are_valid = all(
        _valid_server_record(state.get(name), allow_none=True)
        for name in ("pending", "last_recovery")
    )
    if not records_are_valid:
        return False
    pending = state.get("pending")
    recovered = state.get("last_recovery")
    return (pending is None or pending["sequence"] == sequence) and (
        recovered is None or recovered["sequence"] <= sequence
    )


def _valid_server_record(record: Any, *, allow_none: bool) -> bool:
    """Return whether one optional server transition record is valid."""
    if record is None:
        return allow_none
    return (
        isinstance(record, Mapping)
        and record.get("mode") in {mode.value for mode in ServerTransitionMode}
        and isinstance(record.get("sequence"), int)
        and not isinstance(record.get("sequence"), bool)
        and record["sequence"] >= 0
        and isinstance(record.get("boot_id"), str)
        and bool(record["boot_id"])
    )


def _dispatch_character(event: CharacterLifecycleEvent) -> tuple[int, int]:
    """Dispatch a character event while isolating each consumer failure."""
    dispatched = 0
    failures = 0
    for consumer in tuple(_CONSUMERS.values()):
        if consumer.on_character is None:
            continue
        try:
            consumer.on_character(event)
        except Exception:
            failures += 1
            logger.log_trace(
                f"Lifecycle consumer '{consumer.key}' failed for character "
                f"#{event.character.id} at sequence {event.sequence}."
            )
        else:
            dispatched += 1
    return dispatched, failures


def _dispatch_server(event: ServerLifecycleEvent) -> tuple[int, int]:
    """Dispatch a server event while isolating each consumer failure."""
    dispatched = 0
    failures = 0
    for consumer in tuple(_CONSUMERS.values()):
        if consumer.on_server is None:
            continue
        try:
            consumer.on_server(event)
        except Exception:
            failures += 1
            logger.log_trace(
                f"Lifecycle consumer '{consumer.key}' failed during "
                f"{event.mode.value}/{event.phase.value} sequence "
                f"{event.sequence}."
            )
        else:
            dispatched += 1
    return dispatched, failures

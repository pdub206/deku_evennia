"""Durable, replay-safe queue for delayed noncombat interactions.

Definitions are code-owned while queued records contain primitives only.  The
queue intentionally permits one action per owner: conflict groups describe why
an action may replace another, not a general player-visible backlog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from evennia.utils import logger
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    LifecycleConsumer,
    LifecycleError,
    ServerLifecycleEvent,
    ServerTransitionMode,
    ServerTransitionPhase,
    UnavailabilityCause,
    register_lifecycle_consumer,
    unregister_lifecycle_consumer,
)
from systems.pulses import PulseEvent, PulseLane

ACTION_ATTRIBUTE = "interact06_action"
ACTION_AUDIT_ATTRIBUTE = "interact06_action_audit"
ACTION_CLOCK_CONFIG_KEY = "interact06_action_clock"
ACTION_SCHEMA_VERSION = 1
ACTION_LIFECYCLE_KEY = "interact06.action_queue"
DEFAULT_AUDIT_LIMIT = 20
MAX_ARGUMENT_ITEMS = 32
MAX_TEXT_LENGTH = 200


class ActionQueueError(ValueError):
    """A definition, request, or stored action violates the queue contract."""


class ActionStatus(str, Enum):
    """Stable states exposed by structured action results and diagnostics."""

    QUEUED = "queued"
    REPLACED = "replaced"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    DECLINED = "declined"
    FAILED = "failed"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class ActionResult:
    """Outcome of scheduling, cancellation, or execution."""

    status: ActionStatus
    action_id: str | None = None
    reason: str = ""


Validator = Callable[[Any, Mapping[str, Any]], bool | str]
ReservationAdapter = Callable[[Any, Mapping[str, Any]], Mapping[str, Any] | None]
ReservationFinalizer = Callable[[Any, Mapping[str, Any]], None]
Executor = Callable[[Any, Mapping[str, Any], Mapping[str, Any]], bool | str | None]


@dataclass(frozen=True)
class ActionDefinition:
    """Code-owned behavior for one stable delayed-action identity."""

    key: str
    conflict_group: str
    validate: Validator
    execute: Executor
    replace_existing: bool = False
    acquire: ReservationAdapter | None = None
    release: ReservationFinalizer | None = None
    commit: ReservationFinalizer | None = None
    survive_reload: bool = True
    disconnect_safe: bool = False

    def __post_init__(self) -> None:
        for value, label in ((self.key, "key"), (self.conflict_group, "group")):
            if not _stable_key(value):
                raise ActionQueueError(f"An action definition needs a stable {label}.")
        if not callable(self.validate) or not callable(self.execute):
            raise ActionQueueError("Action validation and execution must be callable.")
        adapters = (self.acquire, self.release, self.commit)
        if any(adapter is not None and not callable(adapter) for adapter in adapters):
            raise ActionQueueError("Reservation adapters must be callable.")
        if self.acquire is not None and (self.release is None or self.commit is None):
            raise ActionQueueError(
                "Reservations need both release and commit adapters."
            )


_DEFINITIONS: dict[str, ActionDefinition] = {}


def register_action_definition(definition: ActionDefinition) -> None:
    """Register one definition, rejecting ambiguous action identities."""
    if not isinstance(definition, ActionDefinition):
        raise ActionQueueError("Only ActionDefinition instances may be registered.")
    if definition.key in _DEFINITIONS:
        raise ActionQueueError(f"Action definition '{definition.key}' already exists.")
    _DEFINITIONS[definition.key] = definition


def unregister_action_definition(key: str) -> None:
    """Remove a definition, primarily for isolated tests."""
    _DEFINITIONS.pop(str(key).strip().lower(), None)


def schedule_action(
    owner: Any, key: str, arguments: Mapping[str, Any], *, delay: int
) -> ActionResult:
    """Validate, reserve, and persist one action atomically for its owner."""
    definition = _DEFINITIONS.get(str(key).strip().lower())
    if definition is None:
        return ActionResult(ActionStatus.DECLINED, reason="unknown_definition")
    owner_id = getattr(owner, "id", None)
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        return ActionResult(ActionStatus.DECLINED, reason="invalid_owner")
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 1:
        return ActionResult(ActionStatus.DECLINED, reason="invalid_delay")
    try:
        args = _validated_primitives(arguments)
        denial = _validation_reason(definition.validate(owner, args))
    except Exception:
        logger.log_trace(f"Action '{definition.key}' request validation failed.")
        return ActionResult(ActionStatus.DECLINED, reason="validation_failed")
    if denial:
        return ActionResult(ActionStatus.DECLINED, reason=denial)

    with transaction.atomic():
        locked = _locked_owner(owner)
        existing = _read_record(locked, required=False)
        replaced = False
        if existing is not None:
            if (
                not definition.replace_existing
                or existing["conflict_group"] != definition.conflict_group
            ):
                return ActionResult(ActionStatus.DECLINED, reason="conflict")
            _finish(locked, existing, ActionStatus.REPLACED, "replaced")
            replaced = True
        reservation: Mapping[str, Any] = {}
        if definition.acquire is not None:
            try:
                reservation = definition.acquire(locked, args) or {}
                reservation = _validated_primitives(reservation)
            except Exception:
                logger.log_trace(f"Action '{definition.key}' reservation failed.")
                return ActionResult(ActionStatus.DECLINED, reason="reservation_failed")
        created = current_action_sequence()
        action_id = uuid4().hex
        record = {
            "version": ACTION_SCHEMA_VERSION,
            "id": action_id,
            "definition": definition.key,
            "owner_id": owner_id,
            "arguments": args,
            "created_token": created,
            "due_token": created + delay,
            "conflict_group": definition.conflict_group,
            "reservation": reservation,
            "status": ActionStatus.QUEUED.value,
            "survive_reload": definition.survive_reload,
            "disconnect_safe": definition.disconnect_safe,
            "last_consumed_token": None,
            "reason": "",
        }
        locked.attributes.add(ACTION_ATTRIBUTE, record)
    return ActionResult(
        ActionStatus.REPLACED if replaced else ActionStatus.QUEUED, action_id
    )


def cancel_action(owner: Any, *, reason: str = "cancelled") -> ActionResult:
    """Cancel an owner's queued action and release its reservation once."""
    with transaction.atomic():
        locked = _locked_owner(owner)
        record = _read_record(locked, required=False)
        if record is None:
            return ActionResult(ActionStatus.DECLINED, reason="no_action")
        _finish(locked, record, ActionStatus.CANCELLED, _bounded_reason(reason))
        return ActionResult(ActionStatus.CANCELLED, record["id"], reason)


def inspect_action(owner: Any) -> dict[str, Any] | None:
    """Return a detached, validated record for staff diagnostics."""
    record = _read_record(owner, required=False)
    return dict(record) if record is not None else None


def action_audit(owner: Any) -> tuple[dict[str, Any], ...]:
    """Return the bounded terminal history for one owner."""
    raw = owner.attributes.get(ACTION_AUDIT_ATTRIBUTE)
    if raw is None:
        return ()
    if not isinstance(raw, Mapping) or raw.get("version") != ACTION_SCHEMA_VERSION:
        raise ActionQueueError("Action audit state is malformed.")
    entries = raw.get("entries")
    if (
        not isinstance(entries, Sequence)
        or isinstance(entries, (str, bytes))
        or not all(isinstance(row, Mapping) for row in entries)
    ):
        raise ActionQueueError("Action audit entries are malformed.")
    return tuple(dict(row) for row in entries)


def repair_action(owner: Any) -> ActionResult:
    """Clear malformed or quarantined state without guessing at a reservation."""
    raw = owner.attributes.get(ACTION_ATTRIBUTE)
    if raw is None:
        return ActionResult(ActionStatus.DECLINED, reason="no_action")
    try:
        record = _validate_record(raw)
    except ActionQueueError:
        owner.attributes.remove(ACTION_ATTRIBUTE)
        return ActionResult(ActionStatus.CANCELLED, reason="malformed_repaired")
    if record["status"] != ActionStatus.QUARANTINED.value:
        return ActionResult(ActionStatus.DECLINED, record["id"], "not_quarantined")
    owner.attributes.remove(ACTION_ATTRIBUTE)
    _append_audit(owner, record, ActionStatus.CANCELLED, "staff_repair")
    return ActionResult(ActionStatus.CANCELLED, record["id"], "staff_repair")


def process_action_pulse(event: PulseEvent) -> None:
    """Consume every due action independently on one durable action token."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.ACTIONS:
        raise ActionQueueError("Action processing requires an actions pulse.")
    _write_action_sequence(event.sequence)
    from typeclasses.characters import Character

    owners = Character.objects.filter_family(
        db_attributes__db_key=ACTION_ATTRIBUTE
    ).distinct()
    for owner in owners.iterator():
        try:
            _process_owner(owner, event)
        except Exception:
            logger.log_trace(
                f"Action processing failed for owner #{owner.id} at token {event.sequence}."
            )


def current_action_sequence() -> int:
    """Return the last persisted action-lane token."""
    from evennia.server.models import ServerConfig

    value = ServerConfig.objects.conf(ACTION_CLOCK_CONFIG_KEY)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _process_owner(owner: Any, event: PulseEvent) -> None:
    """Revalidate and execute one due record after marking it consumed."""
    with transaction.atomic():
        locked = _locked_owner(owner)
        try:
            record = _read_record(locked, required=True)
        except ActionQueueError:
            _quarantine_raw(locked, "malformed_record")
            return
        if record is None or record["due_token"] > event.sequence:
            return
        if record["last_consumed_token"] is not None:
            return
        definition = _DEFINITIONS.get(record["definition"])
        if definition is None:
            _quarantine(locked, record, "unknown_definition")
            return
        denial = _validation_reason(definition.validate(locked, record["arguments"]))
        if denial:
            _finish(locked, record, ActionStatus.CANCELLED, denial)
            return
        record["last_consumed_token"] = event.sequence
        record["status"] = "executing"
        locked.attributes.add(ACTION_ATTRIBUTE, record)

    try:
        outcome = definition.execute(owner, record["arguments"], record["reservation"])
        failure = _validation_reason(outcome)
        if failure:
            raise ActionQueueError(failure)
        if definition.commit is not None:
            definition.commit(owner, record["reservation"])
    except Exception as err:
        logger.log_trace(f"Action '{definition.key}' execution failed for #{owner.id}.")
        _finish(
            owner, record, ActionStatus.FAILED, _bounded_reason(str(err) or "failed")
        )
        return
    _finish(owner, record, ActionStatus.COMPLETED, "completed", release=False)


def _finish(
    owner: Any,
    record: dict[str, Any],
    status: ActionStatus,
    reason: str,
    *,
    release: bool = True,
) -> None:
    """Remove active state first, then finalize reservation and audit once."""
    active = owner.attributes.get(ACTION_ATTRIBUTE)
    if not isinstance(active, Mapping) or active.get("id") != record["id"]:
        return
    owner.attributes.remove(ACTION_ATTRIBUTE)
    definition = _DEFINITIONS.get(record["definition"])
    if release and definition is not None and definition.release is not None:
        try:
            definition.release(owner, record["reservation"])
        except Exception:
            logger.log_trace(f"Action '{definition.key}' reservation release failed.")
            status, reason = ActionStatus.QUARANTINED, "reservation_release_failed"
            record["status"] = status.value
            record["reason"] = reason
            owner.attributes.add(ACTION_ATTRIBUTE, record)
            return
    _append_audit(owner, record, status, reason)


def _append_audit(
    owner: Any, record: Mapping[str, Any], status: ActionStatus, reason: str
) -> None:
    """Append one bounded primitive terminal summary."""
    try:
        entries = list(action_audit(owner))
    except ActionQueueError:
        entries = []
    entries.append(
        {
            "id": record["id"],
            "definition": record["definition"],
            "status": status.value,
            "reason": _bounded_reason(reason),
            "created_token": record["created_token"],
            "due_token": record["due_token"],
            "last_consumed_token": record["last_consumed_token"],
        }
    )
    limit = getattr(settings, "GAME_ACTION_AUDIT_LIMIT", DEFAULT_AUDIT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        limit = DEFAULT_AUDIT_LIMIT
    owner.attributes.add(
        ACTION_AUDIT_ATTRIBUTE,
        {"version": ACTION_SCHEMA_VERSION, "entries": entries[-limit:]},
    )


def _on_character_lifecycle(event: CharacterLifecycleEvent) -> None:
    """Cancel on final disconnect unless the definition explicitly opts in."""
    if event.availability is not CharacterAvailability.UNAVAILABLE:
        return
    if event.cause not in {
        UnavailabilityCause.DISCONNECT,
        UnavailabilityCause.COLD_SHUTDOWN,
    }:
        return
    record = _read_record(event.character, required=False)
    if record is None:
        return
    if event.cause is UnavailabilityCause.DISCONNECT and record["disconnect_safe"]:
        return
    cancel_action(event.character, reason=event.cause.value)


def _on_server_lifecycle(event: ServerLifecycleEvent) -> None:
    """Apply hot-reload opt-in and unconditional cold-restart cancellation."""
    if event.phase is not ServerTransitionPhase.PREPARE:
        return
    from typeclasses.characters import Character

    for owner in (
        Character.objects.filter_family(db_attributes__db_key=ACTION_ATTRIBUTE)
        .distinct()
        .iterator()
    ):
        try:
            record = _read_record(owner, required=True)
            if (
                event.mode is ServerTransitionMode.COLD_RESTART
                or not record["survive_reload"]
            ):
                cancel_action(owner, reason=event.mode.value)
        except Exception:
            logger.log_trace(f"Could not apply action lifecycle policy to #{owner.id}.")


def _register_lifecycle_consumer() -> None:
    consumer = LifecycleConsumer(
        ACTION_LIFECYCLE_KEY,
        on_character=_on_character_lifecycle,
        on_server=_on_server_lifecycle,
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(ACTION_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


def _locked_owner(owner: Any) -> Any:
    """Return the row-locked object matching an action owner."""
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.select_for_update().get(pk=owner.pk)


def _read_record(owner: Any, *, required: bool) -> dict[str, Any] | None:
    raw = owner.attributes.get(ACTION_ATTRIBUTE)
    if raw is None and not required:
        return None
    return _validate_record(raw)


def _validate_record(raw: Any) -> dict[str, Any]:
    keys = {
        "version",
        "id",
        "definition",
        "owner_id",
        "arguments",
        "created_token",
        "due_token",
        "conflict_group",
        "reservation",
        "status",
        "survive_reload",
        "disconnect_safe",
        "last_consumed_token",
        "reason",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != keys
        or raw.get("version") != ACTION_SCHEMA_VERSION
    ):
        raise ActionQueueError("Action record has an invalid schema.")
    for key in ("id", "definition", "conflict_group"):
        if not _stable_key(raw[key]):
            raise ActionQueueError("Action record has an invalid identity.")
    for key in ("owner_id", "created_token", "due_token"):
        if isinstance(raw[key], bool) or not isinstance(raw[key], int) or raw[key] < 0:
            raise ActionQueueError("Action record has an invalid numeric field.")
    if raw["owner_id"] < 1 or raw["due_token"] <= raw["created_token"]:
        raise ActionQueueError("Action record timing or ownership is invalid.")
    if raw["status"] not in {
        ActionStatus.QUEUED.value,
        "executing",
        ActionStatus.QUARANTINED.value,
    }:
        raise ActionQueueError("Action record has an invalid active status.")
    if not isinstance(raw["survive_reload"], bool) or not isinstance(
        raw["disconnect_safe"], bool
    ):
        raise ActionQueueError("Action lifecycle flags are invalid.")
    consumed = raw["last_consumed_token"]
    if consumed is not None and (
        isinstance(consumed, bool) or not isinstance(consumed, int) or consumed < 0
    ):
        raise ActionQueueError("Action consumed token is invalid.")
    if not isinstance(raw["reason"], str):
        raise ActionQueueError("Action reason is invalid.")
    record = dict(raw)
    record["arguments"] = _validated_primitives(raw["arguments"])
    record["reservation"] = _validated_primitives(raw["reservation"])
    return record


def _validated_primitives(value: Any, *, depth: int = 0) -> Any:
    """Detach a bounded JSON-like value while rejecting runtime objects."""
    if depth > 4:
        raise ActionQueueError("Action data is nested too deeply.")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LENGTH:
            raise ActionQueueError("Action text is too long.")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_ARGUMENT_ITEMS or not all(
            isinstance(key, str) and _stable_key(key) for key in value
        ):
            raise ActionQueueError("Action mappings need bounded stable string keys.")
        return {
            key: _validated_primitives(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ARGUMENT_ITEMS:
            raise ActionQueueError("Action sequences are too large.")
        return [_validated_primitives(item, depth=depth + 1) for item in value]
    raise ActionQueueError("Action records may contain only bounded primitives.")


def _validation_reason(result: bool | str | None) -> str:
    if result is True or result is None:
        return ""
    if result is False:
        return "denied"
    if isinstance(result, str):
        return _bounded_reason(result)
    raise ActionQueueError("Action validation must return bool, text, or None.")


def _stable_key(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 80
        and all(char in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in value)
    )


def _bounded_reason(reason: str) -> str:
    return str(reason)[:MAX_TEXT_LENGTH]


def _write_action_sequence(sequence: int) -> None:
    from evennia.server.models import ServerConfig

    ServerConfig.objects.conf(ACTION_CLOCK_CONFIG_KEY, value=sequence)


def _quarantine(owner: Any, record: dict[str, Any], reason: str) -> None:
    record["status"] = ActionStatus.QUARANTINED.value
    record["reason"] = reason
    owner.attributes.add(ACTION_ATTRIBUTE, record)


def _quarantine_raw(owner: Any, reason: str) -> None:
    logger.log_err(f"Malformed queued action on owner #{owner.id}: {reason}.")


_register_lifecycle_consumer()

"""MOB-06's safe, composable registry for code-owned NPC special behaviors.

Prototype and live-object data name registered specials and carry only a small
JSON-like configuration.  Handlers stay in this module (or another imported
Python module), which deliberately prevents authored prototypes from becoming
an execution or lock-bypass surface.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from evennia.utils import logger

MOBILE_SPECIALS_ATTRIBUTE = "mobile_specials"
MOBILE_SPECIALS_STATE_ATTRIBUTE = "mobile_special_state"
MOBILE_SPECIALS_VERSION = 1
MOBILE_SPECIALS_STATE_VERSION = 1
MAX_DISPATCH_DEPTH = 3
EVENTS = frozenset(
    {
        "decision",
        "arrival",
        "departure",
        "speech",
        "service",
        "combat",
        "injury",
        "death",
        "spawn",
        "reset",
    }
)
OUTCOMES = frozenset({"acted", "declined", "blocked", "deferred", "failed"})


class MobileSpecialError(ValueError):
    """A special definition or its persisted data is unsafe or invalid."""


@dataclass(frozen=True)
class SpecialEvent:
    """A narrow, object-free-at-rest context supplied to one dispatch."""

    hook: str
    actor: Any | None = None
    target: Any | None = None
    token: int | None = None
    text: str = ""
    language: str | None = None
    service: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpecialOutcome:
    """One behavior's structured, safe result for an event."""

    status: str
    reason: str = ""


@dataclass(frozen=True)
class SpecialDispatchResult:
    """The independently recorded results for one NPC event."""

    npc_id: int | None
    hook: str
    outcomes: tuple[tuple[str, SpecialOutcome], ...]

    @property
    def acted(self) -> bool:
        """Whether exactly one behavior consumed this event's action budget."""
        return any(result.status == "acted" for _, result in self.outcomes)


@dataclass(frozen=True)
class MobileSpecialDefinition:
    """A code-owned special with explicit dispatch and conflict semantics."""

    key: str
    hooks: frozenset[str]
    priority: int
    handler: Callable[[Any, SpecialEvent, Mapping[str, Any]], SpecialOutcome]
    conflicts: frozenset[str] = frozenset()
    validate_config: Callable[[Any], dict[str, Any]] | None = None


_SPECIALS: dict[str, MobileSpecialDefinition] = {}
_dispatch_depth: ContextVar[int] = ContextVar(
    "mobile_special_dispatch_depth", default=0
)


def special_keys() -> tuple[str, ...]:
    """Return the current code-owned special keys for builder diagnostics."""
    return tuple(sorted(_SPECIALS))


def register_special(definition: MobileSpecialDefinition) -> None:
    """Register one uniquely named behavior; authored data cannot register code."""
    if (
        not isinstance(definition, MobileSpecialDefinition)
        or not _safe_key(definition.key)
        or not isinstance(definition.hooks, frozenset)
        or not definition.hooks
        or not definition.hooks.issubset(EVENTS)
        or isinstance(definition.priority, bool)
        or not isinstance(definition.priority, int)
        or not callable(definition.handler)
        or not isinstance(definition.conflicts, frozenset)
        or not all(_safe_key(key) for key in definition.conflicts)
        or (
            definition.validate_config is not None
            and not callable(definition.validate_config)
        )
    ):
        raise MobileSpecialError("Special definitions have invalid code-owned fields.")
    if definition.key in _SPECIALS:
        raise MobileSpecialError(f"Special '{definition.key}' is already registered.")
    _SPECIALS[definition.key] = definition


def default_mobile_specials() -> dict[str, Any]:
    """Return the empty, safe special assignment persisted on new templates."""
    return {"version": MOBILE_SPECIALS_VERSION, "behaviors": []}


def validate_mobile_specials(raw: Any) -> dict[str, Any]:
    """Strictly validate authored assignments before they are saved or spawned."""
    if not isinstance(raw, Mapping) or set(raw) != {"version", "behaviors"}:
        raise MobileSpecialError("Mobile specials have an invalid shape.")
    if raw["version"] != MOBILE_SPECIALS_VERSION:
        raise MobileSpecialError("Mobile specials have an unsupported version.")
    entries = raw["behaviors"]
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise MobileSpecialError("Mobile specials must be a list.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        key, config = _validate_entry(entry)
        if key in seen:
            raise MobileSpecialError("A mobile special may be assigned only once.")
        seen.add(key)
        normalized.append({"key": key, "config": config})
    return {"version": MOBILE_SPECIALS_VERSION, "behaviors": normalized}


def mobile_specials(npc: Any) -> dict[str, Any]:
    """Read strict persisted assignments, with absence meaning no specials."""
    raw = npc.attributes.get(MOBILE_SPECIALS_ATTRIBUTE)
    return default_mobile_specials() if raw is None else validate_mobile_specials(raw)


def set_mobile_specials(npc: Any, raw: Any) -> dict[str, Any]:
    """Persist a detached validated assignment for this one live NPC."""
    normalized = validate_mobile_specials(raw)
    npc.attributes.add(MOBILE_SPECIALS_ATTRIBUTE, normalized)
    return deepcopy(normalized)


def dispatch_specials(npc: Any, event: SpecialEvent) -> SpecialDispatchResult:
    """Run matching specials deterministically, bounded to one action per event.

    A broken stored entry is skipped independently here.  Builders cannot save
    one, but this makes historic data and a temporarily unavailable code module
    fail closed without silencing the NPC's other valid assignments.
    """
    if not _valid_event(event):
        raise MobileSpecialError("Mobile special event is invalid.")
    depth = _dispatch_depth.get()
    npc_id = getattr(npc, "id", None)
    if depth >= MAX_DISPATCH_DEPTH:
        return SpecialDispatchResult(
            npc_id,
            event.hook,
            (("dispatch", SpecialOutcome("blocked", "depth_limit")),),
        )
    marker = _dispatch_depth.set(depth + 1)
    try:
        entries = _resilient_entries(npc)
        selected = _ordered_entries(entries, event.hook)
        results: list[tuple[str, SpecialOutcome]] = []
        acted = False
        accepted: set[str] = set()
        for key, config, definition in selected:
            conflict = next(
                (
                    other
                    for other in accepted
                    if other in definition.conflicts
                    or key in _SPECIALS[other].conflicts
                ),
                None,
            )
            if conflict is not None:
                results.append((key, SpecialOutcome("blocked", "conflict")))
                continue
            if acted:
                results.append((key, SpecialOutcome("blocked", "action_consumed")))
                continue
            try:
                outcome = definition.handler(npc, event, deepcopy(config))
                outcome = _validate_outcome(outcome)
            except Exception:
                logger.log_trace(
                    "MOB-06 special failed: "
                    f"event={event.hook} token={event.token!r} npc=#{npc_id} behavior={key}."
                )
                outcome = SpecialOutcome("failed", "exception")
            results.append((key, outcome))
            if outcome.status != "failed":
                accepted.add(key)
            if outcome.status == "acted":
                acted = True
        return SpecialDispatchResult(npc_id, event.hook, tuple(results))
    finally:
        _dispatch_depth.reset(marker)


def dispatch_room_specials(
    room: Any, event: SpecialEvent
) -> tuple[SpecialDispatchResult, ...]:
    """Deliver an event to room NPCs in stable dbref order and isolate failures."""
    candidates = getattr(room, "contents_get", lambda **_: [])(content_type="character")
    results = []
    for npc in sorted(
        candidates, key=lambda candidate: getattr(candidate, "id", 0) or 0
    ):
        if getattr(getattr(npc, "db", None), "is_player_character", None) is False:
            try:
                results.append(dispatch_specials(npc, event))
            except Exception:
                logger.log_trace(
                    f"MOB-06 room event failed: event={event.hook} npc=#{getattr(npc, 'id', None)}."
                )
    return tuple(results)


def _resilient_entries(
    npc: Any,
) -> list[tuple[str, dict[str, Any], MobileSpecialDefinition]]:
    """Keep valid entries usable when old live data has a bad sibling entry."""
    raw = npc.attributes.get(MOBILE_SPECIALS_ATTRIBUTE)
    if raw is None:
        return []
    if not isinstance(raw, Mapping) or raw.get("version") != MOBILE_SPECIALS_VERSION:
        return []
    entries = raw.get("behaviors")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        return []
    valid = []
    seen: set[str] = set()
    for entry in entries:
        try:
            key, config = _validate_entry(entry)
        except MobileSpecialError:
            continue
        if key in seen:
            continue
        seen.add(key)
        definition = _SPECIALS.get(key)
        if definition is not None:
            valid.append((key, config, definition))
    return valid


def _ordered_entries(
    entries: list[tuple[str, dict[str, Any], MobileSpecialDefinition]], hook: str
) -> list[tuple[str, dict[str, Any], MobileSpecialDefinition]]:
    return sorted(
        (entry for entry in entries if hook in entry[2].hooks),
        key=lambda entry: (entry[2].priority, entry[0]),
    )


def _validate_entry(entry: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(entry, Mapping) or set(entry) != {"key", "config"}:
        raise MobileSpecialError("A mobile special entry has an invalid shape.")
    key = entry["key"]
    definition = _SPECIALS.get(key) if isinstance(key, str) else None
    if definition is None:
        raise MobileSpecialError("A mobile special is unknown or unavailable.")
    config = _primitive_copy(entry["config"])
    if not isinstance(config, dict):
        raise MobileSpecialError("Mobile special configuration must be a mapping.")
    if definition.validate_config is not None:
        config = definition.validate_config(config)
    return key, config


def _primitive_copy(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_primitive_copy(item) for item in value]
    if isinstance(value, Mapping) and all(
        isinstance(key, str) and key for key in value
    ):
        return {key: _primitive_copy(item) for key, item in value.items()}
    raise MobileSpecialError("Mobile special data must contain primitive values only.")


def _safe_key(key: Any) -> bool:
    return isinstance(key, str) and bool(key) and len(key) <= 64


def _valid_event(event: Any) -> bool:
    return (
        isinstance(event, SpecialEvent)
        and event.hook in EVENTS
        and (
            event.token is None
            or (isinstance(event.token, int) and not isinstance(event.token, bool))
        )
        and isinstance(event.text, str)
        and (event.language is None or isinstance(event.language, str))
        and (event.service is None or isinstance(event.service, str))
        and isinstance(event.data, Mapping)
    )


def _validate_outcome(outcome: Any) -> SpecialOutcome:
    if (
        not isinstance(outcome, SpecialOutcome)
        or outcome.status not in OUTCOMES
        or not isinstance(outcome.reason, str)
        or len(outcome.reason) > 128
    ):
        raise MobileSpecialError("Mobile special returned an invalid outcome.")
    return outcome


def _empty_config(config: Any) -> dict[str, Any]:
    if config != {}:
        raise MobileSpecialError("This mobile special does not accept configuration.")
    return {}


def _guard(npc: Any, event: SpecialEvent, config: Mapping[str, Any]) -> SpecialOutcome:
    if event.hook != "decision":
        return SpecialOutcome("declined", "unsupported_event")
    from systems.attacks import can_attack
    from systems.combat import is_fighting, start_fight
    from systems.mobile_policy import can_detect, may_enter_combat

    if is_fighting(npc) or not may_enter_combat(npc).allowed or npc.location is None:
        return SpecialOutcome("declined", "ineligible")
    targets = [
        target
        for target in npc.location.contents_get(content_type="character")
        if target is not npc
        and not is_fighting(target)
        and can_detect(npc, target).allowed
        and can_attack(npc, target).allowed
    ]
    target = min(targets, key=lambda candidate: candidate.id, default=None)
    if target is None:
        return SpecialOutcome("declined", "no_target")
    if not start_fight(npc, target).accepted:
        return SpecialOutcome("blocked", "combat_denied")
    return SpecialOutcome("acted", "guard_attack")


def _scavenger(
    npc: Any, event: SpecialEvent, config: Mapping[str, Any]
) -> SpecialOutcome:
    if event.hook != "decision":
        return SpecialOutcome("declined", "unsupported_event")
    from systems.mobile_policy import scavenge

    result = scavenge(npc)
    return (
        SpecialOutcome("acted", "scavenge")
        if result.allowed
        else SpecialOutcome("declined", result.reason)
    )


def _speaker_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, Mapping) or set(config) != {"trigger", "response"}:
        raise MobileSpecialError("Speaker configuration requires trigger and response.")
    trigger, response = config["trigger"], config["response"]
    if not all(
        isinstance(value, str) and value.strip() for value in (trigger, response)
    ):
        raise MobileSpecialError("Speaker trigger and response must be non-empty text.")
    if len(trigger) > 80 or len(response) > 400:
        raise MobileSpecialError("Speaker configuration is too long.")
    return {"trigger": trigger.strip().lower(), "response": response.strip()}


def _speaker(
    npc: Any, event: SpecialEvent, config: Mapping[str, Any]
) -> SpecialOutcome:
    if event.hook not in {"speech", "service"} or event.actor is None:
        return SpecialOutcome("declined", "unsupported_event")
    if event.target not in {None, npc} or config["trigger"] not in event.text.lower():
        return SpecialOutcome("declined", "no_match")
    if npc.location is None or event.actor.location is not npc.location:
        return SpecialOutcome("blocked", "not_colocated")
    from commands.communication import send_speech

    send_speech(npc, config["response"], recipients=(event.actor,))
    return SpecialOutcome("acted", "spoke")


def _trigger_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, Mapping) or set(config) != {"event", "message", "once"}:
        raise MobileSpecialError(
            "Trigger configuration requires event, message, and once."
        )
    event, message, once = config["event"], config["message"], config["once"]
    if (
        event not in EVENTS
        or not isinstance(message, str)
        or not message.strip()
        or not isinstance(once, bool)
    ):
        raise MobileSpecialError("Trigger configuration has invalid values.")
    if len(message) > 400:
        raise MobileSpecialError("Trigger message is too long.")
    return {"event": event, "message": message.strip(), "once": once}


def _unique_trigger(
    npc: Any, event: SpecialEvent, config: Mapping[str, Any]
) -> SpecialOutcome:
    if event.hook != config["event"]:
        return SpecialOutcome("declined", "no_match")
    state = npc.attributes.get(
        MOBILE_SPECIALS_STATE_ATTRIBUTE,
        {"version": MOBILE_SPECIALS_STATE_VERSION, "unique_trigger_fired": False},
    )
    if (
        not isinstance(state, Mapping)
        or set(state) != {"version", "unique_trigger_fired"}
        or state.get("version") != MOBILE_SPECIALS_STATE_VERSION
        or not isinstance(state.get("unique_trigger_fired"), bool)
    ):
        return SpecialOutcome("blocked", "invalid_state")
    state = _primitive_copy(state)
    fired = bool(state.get("unique_trigger_fired", False))
    if config["once"] and fired:
        return SpecialOutcome("declined", "already_fired")
    if npc.location is None:
        return SpecialOutcome("blocked", "off_grid")
    npc.location.msg_contents(config["message"], from_obj=npc)
    if config["once"]:
        state["unique_trigger_fired"] = True
        npc.attributes.add(MOBILE_SPECIALS_STATE_ATTRIBUTE, state)
    return SpecialOutcome("acted", "triggered")


def _unavailable_adapter(
    npc: Any, event: SpecialEvent, config: Mapping[str, Any]
) -> SpecialOutcome:
    return SpecialOutcome("deferred", "dependency_unavailable")


def _register_builtin_specials() -> None:
    register_special(
        MobileSpecialDefinition(
            "guard", frozenset({"decision"}), 10, _guard, validate_config=_empty_config
        )
    )
    register_special(
        MobileSpecialDefinition(
            "scavenger",
            frozenset({"decision"}),
            20,
            _scavenger,
            validate_config=_empty_config,
        )
    )
    register_special(
        MobileSpecialDefinition(
            "speaker",
            frozenset({"speech", "service"}),
            10,
            _speaker,
            validate_config=_speaker_config,
        )
    )
    register_special(
        MobileSpecialDefinition(
            "unique_trigger",
            EVENTS,
            30,
            _unique_trigger,
            validate_config=_trigger_config,
        )
    )
    for key in ("shopkeeper", "trainer", "caster", "healer"):
        register_special(
            MobileSpecialDefinition(
                key,
                frozenset({"service"}),
                10,
                _unavailable_adapter,
                validate_config=_empty_config,
            )
        )


_register_builtin_specials()

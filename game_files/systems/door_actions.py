"""INTERACT-01B manipulation service for doors, containers, keys, and picks."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from systems.checks import CheckError, CheckRequest, CheckResult, resolve_check
from systems.doors import DoorError, DoorState, door_state, paired_exit, transition_door

THIEVES_TOOLS_KIND = "thieves_tools"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


def _no_pending_traversal(_exit: Any) -> bool:
    """Default adapter before INTERACT-06 supplies reservations."""
    return False


_pending_traversal_checker: Callable[[Any], bool] = _no_pending_traversal


class DoorActionError(ValueError):
    """A safe denial of a requested manipulation."""


@dataclass(frozen=True)
class DoorActionResult:
    """One committed transition or consumed picking attempt."""

    action: str
    changed: bool
    state: DoorState
    check: CheckResult | None = None


def set_pending_traversal_checker(checker: Callable[[Any], bool]) -> None:
    """Install INTERACT-06's exact-exit reservation reader when it exists."""
    global _pending_traversal_checker
    if not callable(checker):
        raise TypeError("The pending traversal checker must be callable.")
    _pending_traversal_checker = checker


def item_key_kind(item: Any) -> str | None:
    """Return a canonical key identity, rejecting malformed key objects."""
    if str(item.attributes.get("type") or "").casefold() != "key":
        return None
    value = item.attributes.get("key_kind")
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise DoorActionError("That key is not configured correctly.")
    return value


def has_matching_key(actor: Any, key_kind: str | None) -> bool:
    """Check only ordinary items carried directly by ``actor``."""
    if key_kind is None:
        return False
    for item in actor.contents:
        try:
            if item_key_kind(item) == key_kind:
                return True
        except DoorActionError:
            continue
    return False


def has_thieves_tools(actor: Any) -> bool:
    """Return whether the actor directly carries the configured tool item."""
    return any(
        str(item.attributes.get("type") or "").casefold() == "other"
        and str(item.attributes.get("tool_kind") or "").casefold() == THIEVES_TOOLS_KIND
        for item in actor.contents
    )


def manipulate_target(
    actor: Any,
    target: Any,
    action: str,
    *,
    roller: Callable[[int], int] | None = None,
) -> DoorActionResult:
    """Revalidate and perform one door/container action for a PC or NPC."""
    action = action.casefold()
    if action not in {"open", "close", "lock", "unlock", "pick"}:
        raise DoorActionError("Unknown door action.")
    state = _usable_state(actor, target)

    if action == "open":
        if state.open:
            raise DoorActionError("It is already open.")
        if state.locked:
            raise DoorActionError("It is locked.")
        return _transition(actor, target, action, state, True, False)
    if action == "close":
        if not state.open:
            raise DoorActionError("It is already closed.")
        if _is_exit(target) and _pending_traversal_checker(target):
            raise DoorActionError("Someone is already moving through it.")
        return _transition(actor, target, action, state, False, False)
    if action == "lock":
        if state.open:
            raise DoorActionError("Close it first.")
        if state.locked:
            raise DoorActionError("It is already locked.")
        if not has_matching_key(actor, state.key_kind):
            raise DoorActionError("You do not have the right key.")
        return _transition(actor, target, action, state, False, True)
    if action == "unlock":
        if not state.locked:
            raise DoorActionError("It is already unlocked.")
        if not has_matching_key(actor, state.key_kind):
            raise DoorActionError("You do not have the right key.")
        return _transition(actor, target, action, state, False, False)

    if not state.locked:
        raise DoorActionError("It is already unlocked.")
    if not state.pickable or state.pick_dc is None:
        raise DoorActionError("That lock cannot be picked.")
    if not has_thieves_tools(actor):
        raise DoorActionError("You need thieves' tools in hand.")
    kwargs = {"roller": roller} if roller is not None else {}
    try:
        check = resolve_check(
            CheckRequest(
                actor,
                "Dexterity",
                state.pick_dc,
                tool=THIEVES_TOOLS_KIND,
                action_key="lock_picking",
            ),
            **kwargs,
        )
    except CheckError as exc:
        raise DoorActionError("You cannot attempt that lock right now.") from exc
    # The attempt is consumed by rolling. Revalidation prevents a stale success.
    current = _usable_state(actor, target)
    if current != state:
        raise DoorActionError("The lock changed before you could finish.")
    if not check.success:
        return DoorActionResult(action, False, state, check)
    result = _transition(actor, target, action, current, False, False)
    return DoorActionResult(action, result.changed, result.state, check)


def _usable_state(actor: Any, target: Any) -> DoorState:
    """Fail closed for remote, inaccessible, hidden, or malformed targets."""
    if not _is_local(actor, target) or not target.access(
        actor, "interact", default=True
    ):
        raise DoorActionError("You cannot manipulate that.")
    try:
        state = door_state(target)
        if state is None:
            raise DoorActionError("That cannot be opened or closed.")
        if state.hidden:
            raise DoorActionError("You cannot manipulate that.")
        if state.pair_key:
            paired_exit(target)
        return state
    except DoorError as exc:
        raise DoorActionError("That cannot be manipulated right now.") from exc


def _transition(
    actor: Any,
    target: Any,
    action: str,
    expected: DoorState,
    open_: bool,
    locked: bool,
) -> DoorActionResult:
    """Revalidate immediately, then commit through INTERACT-01A."""
    if _usable_state(actor, target) != expected:
        raise DoorActionError("That changed before you could finish.")
    try:
        mutation = transition_door(
            target,
            open=open_,
            locked=locked,
            state_id=f"interact:{action}:{uuid4().hex}",
        )
    except DoorError as exc:
        raise DoorActionError("That changed before you could finish.") from exc
    if mutation.status != "changed":
        raise DoorActionError("That changed before you could finish.")
    return DoorActionResult(action, True, mutation.state)


def _is_exit(target: Any) -> bool:
    return getattr(target, "destination", None) is not None


def _is_local(actor: Any, target: Any) -> bool:
    if _is_exit(target):
        return target.location == actor.location
    return target.location in {actor, actor.location}

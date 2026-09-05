"""Queued flee routing for COMBAT-03 without weakening ordinary movement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from evennia.objects.models import ObjectDB
from systems.dice import roll

_FLEE_TOKEN = object()


@dataclass(frozen=True)
class FleeExitDecision:
    """Side-effect-free validation of one possible flee route."""

    allowed: bool
    exit: Any | None = None
    reason: str = ""


FleeSelector = Callable[[tuple[Any, ...]], Any]


def combat_flee_active(actor: Any, token: Any) -> bool:
    """Recognize only the private token held while this service moves an actor."""
    return (
        token is _FLEE_TOKEN
        and getattr(actor.ndb, "_combat_flee_token", None) is _FLEE_TOKEN
    )


def flee_exit_decision(actor: Any, exit_obj: Any) -> FleeExitDecision:
    """Check an exit's normal traversal eligibility without moving."""
    from typeclasses.exits import Exit

    if (
        not isinstance(exit_obj, Exit)
        or actor.location is None
        or exit_obj.location != actor.location
    ):
        return FleeExitDecision(False, reason="invalid_route")
    if exit_obj.destination is None:
        return FleeExitDecision(False, reason="invalid_route")
    if not exit_obj.access(actor, "traverse", default=True):
        return FleeExitDecision(False, reason="blocked_route")
    return FleeExitDecision(True, exit_obj)


def eligible_flee_exits(actor: Any) -> tuple[Any, ...]:
    """Return all current eligible room exits in stable order."""
    if actor.location is None:
        return ()
    exits = [
        exit_obj
        for exit_obj in actor.location.exits
        if flee_exit_decision(actor, exit_obj).allowed
    ]
    return tuple(
        sorted(exits, key=lambda exit_obj: (exit_obj.key.casefold(), exit_obj.id))
    )


def select_flee_exit(exits: tuple[Any, ...]) -> Any:
    """Select a random eligible exit through the canonical dice primitive."""
    if not exits:
        raise ValueError("No flee exits are available.")
    return exits[roll(len(exits)) - 1]


def choose_flee_exit(
    actor: Any,
    requested: Any | None = None,
    *,
    selector: FleeSelector = select_flee_exit,
) -> FleeExitDecision:
    """Resolve a requested route or select one eligible exit without moving."""
    if requested is not None:
        return flee_exit_decision(actor, requested)
    exits = eligible_flee_exits(actor)
    if not exits:
        return FleeExitDecision(False, reason="no_route")
    return flee_exit_decision(actor, selector(exits))


def execute_flee_intent(actor: Any, intent: Any) -> bool:
    """Consume a validated intent by attempting normal-hook movement once."""
    if (
        not isinstance(intent, Mapping)
        or set(intent) != {"kind", "exit"}
        or intent.get("kind") != "flee"
    ):
        return False
    try:
        exit_obj = ObjectDB.objects.get(id=intent["exit"])
    except Exception:
        actor.msg("Your escape route is no longer available.")
        return False
    decision = flee_exit_decision(actor, exit_obj)
    if not decision.allowed:
        actor.msg("Your escape route is no longer available.")
        return False
    source = actor.location
    actor.ndb._combat_flee_token = _FLEE_TOKEN
    try:
        # ``move_to`` still invokes the source/destination movement hooks. The
        # route's traverse lock was checked above; the private mode only relaxes
        # the fighting-position denial in Character.at_pre_move.
        moved = actor.move_to(
            exit_obj.destination,
            move_type="combat_flee",
            combat_flee_token=_FLEE_TOKEN,
            use_destination=False,
        )
    finally:
        actor.ndb._combat_flee_token = None
    if not moved or actor.location is source:
        actor.msg("You fail to escape.")
        return False
    actor.msg("You flee from combat.")
    return True

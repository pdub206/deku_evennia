"""Canonical position and action policy shared by characters and commands.

Persistent ``db.position`` stores only a character's voluntary posture. Combat,
effects, and injury systems contribute temporary or derived positions through
``get_imposed_action_positions`` without overwriting that underlying posture.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ActionPolicyError(ValueError):
    """An action, position, or transition is outside the policy contract."""


class Position(str, Enum):
    """DIKU-compatible effective positions, ordered separately by severity."""

    DEAD = "dead"
    DYING = "dying"
    INCAPACITATED = "incapacitated"
    STUNNED = "stunned"
    SLEEPING = "sleeping"
    RESTING = "resting"
    SITTING = "sitting"
    FIGHTING = "fighting"
    STANDING = "standing"


class ActionCategory(str, Enum):
    """Stable action families used by commands and non-command systems."""

    STATE_INDEPENDENT = "state_independent"
    OBSERVE = "observe"
    COMMUNICATE = "communicate"
    MANIPULATE = "manipulate"
    CHANGE_POSITION = "change_position"
    WAKE = "wake"
    MOVE = "move"
    COMBAT = "combat"


class TransitionOutcome(str, Enum):
    """Result of asking the policy to change voluntary posture."""

    CHANGED = "changed"
    ALREADY = "already"
    DENIED = "denied"


_POSITION_RANK: Mapping[Position, int] = MappingProxyType(
    {
        Position.DEAD: 0,
        Position.DYING: 1,
        Position.INCAPACITATED: 2,
        Position.STUNNED: 3,
        Position.SLEEPING: 4,
        Position.RESTING: 5,
        Position.SITTING: 6,
        Position.FIGHTING: 7,
        Position.STANDING: 8,
    }
)

PERSISTENT_POSTURES = frozenset(
    {Position.STANDING, Position.SITTING, Position.RESTING, Position.SLEEPING}
)

ACTION_PERMISSIONS: Mapping[ActionCategory, frozenset[Position]] = MappingProxyType(
    {
        ActionCategory.STATE_INDEPENDENT: frozenset(Position),
        ActionCategory.OBSERVE: frozenset(
            {
                Position.STANDING,
                Position.SITTING,
                Position.RESTING,
                Position.FIGHTING,
            }
        ),
        ActionCategory.COMMUNICATE: frozenset(
            {
                Position.STANDING,
                Position.SITTING,
                Position.RESTING,
                Position.FIGHTING,
            }
        ),
        ActionCategory.MANIPULATE: frozenset(
            {Position.STANDING, Position.SITTING, Position.RESTING}
        ),
        ActionCategory.CHANGE_POSITION: frozenset(
            {Position.STANDING, Position.SITTING, Position.RESTING}
        ),
        ActionCategory.WAKE: frozenset({Position.SLEEPING}),
        ActionCategory.MOVE: frozenset({Position.STANDING}),
        ActionCategory.COMBAT: frozenset({Position.STANDING, Position.FIGHTING}),
    }
)

POSTURE_TRANSITIONS: Mapping[Position, frozenset[Position]] = MappingProxyType(
    {
        Position.STANDING: frozenset(
            {Position.SITTING, Position.RESTING, Position.SLEEPING}
        ),
        Position.SITTING: frozenset(
            {Position.STANDING, Position.RESTING, Position.SLEEPING}
        ),
        Position.RESTING: frozenset(
            {Position.STANDING, Position.SITTING, Position.SLEEPING}
        ),
        Position.SLEEPING: frozenset({Position.SITTING}),
    }
)


@dataclass(frozen=True)
class PositionResolution:
    """The actor's posture and most restrictive effective position."""

    position: Position
    posture: Position | None
    imposed: tuple[Position, ...] = ()
    valid: bool = True


@dataclass(frozen=True)
class ActionDecision:
    """Structured result from checking one attempted action."""

    allowed: bool
    action: ActionCategory
    position: Position
    message: str = ""


@dataclass(frozen=True)
class TransitionResult:
    """Structured result from changing an actor's voluntary posture."""

    outcome: TransitionOutcome
    previous: Position | None
    target: Position
    decision: ActionDecision


def action_allowed(position: Position, action: ActionCategory) -> bool:
    """Return the explicit permission-matrix result for one effective state."""
    if not isinstance(position, Position) or not isinstance(action, ActionCategory):
        raise ActionPolicyError("Position and action checks must use their enums.")
    return position in ACTION_PERMISSIONS[action]


def _coerce_position(value: Any) -> Position | None:
    """Convert one declared position to its enum, returning None if invalid."""
    if isinstance(value, Position):
        return value
    try:
        return Position(str(value).strip().lower())
    except (TypeError, ValueError):
        return None


def resolve_position(actor: Any) -> PositionResolution:
    """Resolve posture plus system-imposed states to the strictest position."""
    attributes = getattr(actor, "db", None)
    raw_posture = attributes.position if attributes is not None else None
    if raw_posture in (None, ""):
        posture = Position.STANDING
    else:
        posture = _coerce_position(raw_posture)
    valid = posture in PERSISTENT_POSTURES

    imposed: list[Position] = []
    source_hook = getattr(actor, "get_imposed_action_positions", None)
    if callable(source_hook):
        for value in source_hook():
            position = _coerce_position(value)
            if position is None:
                valid = False
            else:
                imposed.append(position)

    if not valid:
        return PositionResolution(
            position=Position.INCAPACITATED,
            posture=posture if posture in PERSISTENT_POSTURES else None,
            imposed=tuple(imposed),
            valid=False,
        )

    effective = min((posture, *imposed), key=_POSITION_RANK.__getitem__)
    return PositionResolution(
        position=effective,
        posture=posture,
        imposed=tuple(imposed),
    )


def _denial_message(resolution: PositionResolution, action: ActionCategory) -> str:
    """Return consistent player-facing feedback for a denied action."""
    if not resolution.valid:
        return "You cannot act right now. Please contact staff if this persists."
    if action is ActionCategory.WAKE:
        return "You can only wake while sleeping."
    if resolution.position in {Position.SITTING, Position.RESTING}:
        return "You need to stand before you can do that."
    messages = {
        Position.SLEEPING: (
            "You are asleep and cannot do that. Type |wwake|n to wake up."
        ),
        Position.FIGHTING: "You cannot do that while fighting.",
        Position.STUNNED: "You are stunned and cannot do that.",
        Position.INCAPACITATED: "You are incapacitated and cannot do that.",
        Position.DYING: "You are dying and cannot do that.",
        Position.DEAD: "You are dead and cannot do that.",
    }
    return messages.get(resolution.position, "You cannot do that right now.")


class ActionPolicy:
    """Resolve and enforce action permissions for one character-like actor."""

    def __init__(self, actor: Any) -> None:
        self.actor = actor

    @property
    def position(self) -> Position:
        """Return the actor's current canonical effective position."""
        return resolve_position(self.actor).position

    def check(self, action: ActionCategory) -> ActionDecision:
        """Evaluate an action without producing output or side effects."""
        if not isinstance(action, ActionCategory):
            raise ActionPolicyError("Action checks must use ActionCategory.")
        resolution = resolve_position(self.actor)
        allowed = action_allowed(resolution.position, action)
        if not resolution.valid and action is not ActionCategory.STATE_INDEPENDENT:
            allowed = False
        return ActionDecision(
            allowed=allowed,
            action=action,
            position=resolution.position,
            message="" if allowed else _denial_message(resolution, action),
        )

    def transition(
        self,
        target: Position,
        *,
        action: ActionCategory = ActionCategory.CHANGE_POSITION,
    ) -> TransitionResult:
        """Validate and apply one voluntary posture transition."""
        if not isinstance(target, Position) or target not in PERSISTENT_POSTURES:
            raise ActionPolicyError("A posture transition needs a persistent posture.")
        if action not in {ActionCategory.CHANGE_POSITION, ActionCategory.WAKE}:
            raise ActionPolicyError("A posture transition needs a position action.")

        resolution = resolve_position(self.actor)
        decision = self.check(action)
        previous = resolution.posture
        if not decision.allowed:
            return TransitionResult(
                TransitionOutcome.DENIED, previous, target, decision
            )
        if previous is target:
            return TransitionResult(
                TransitionOutcome.ALREADY, previous, target, decision
            )
        if previous is None or target not in POSTURE_TRANSITIONS[previous]:
            denied = ActionDecision(
                allowed=False,
                action=action,
                position=resolution.position,
                message="You cannot change to that position right now.",
            )
            return TransitionResult(TransitionOutcome.DENIED, previous, target, denied)
        if action is ActionCategory.WAKE and not (
            previous is Position.SLEEPING and target is Position.SITTING
        ):
            denied = ActionDecision(
                allowed=False,
                action=action,
                position=resolution.position,
                message="You can only wake while sleeping.",
            )
            return TransitionResult(TransitionOutcome.DENIED, previous, target, denied)

        self.actor.db.position = target.value
        return TransitionResult(TransitionOutcome.CHANGED, previous, target, decision)

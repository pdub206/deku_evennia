"""Tests for the WORLD-02 position and action policy."""

from unittest.mock import MagicMock, patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.action_policy import (ActionCategory, ActionPolicyError, Position,
                                   TransitionOutcome, action_allowed,
                                   resolve_position)
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from typeclasses.characters import Character

_STUN = EffectDefinition(
    key="test.world02.stun",
    name="Test Stun",
    duration=1,
    conditions=frozenset({"stunned"}),
)
if EFFECT_REGISTRY.get(_STUN.key) is None:
    EFFECT_REGISTRY.register(_STUN)


class TestActionMatrix(EvenniaTest):
    """Every effective position has an explicit set of legal actions."""

    def test_complete_permission_matrix(self):
        independent = {ActionCategory.STATE_INDEPENDENT}
        aware = {
            ActionCategory.STATE_INDEPENDENT,
            ActionCategory.OBSERVE,
            ActionCategory.COMMUNICATE,
        }
        expected = {
            Position.STANDING: {
                *aware,
                ActionCategory.MANIPULATE,
                ActionCategory.CHANGE_POSITION,
                ActionCategory.MOVE,
                ActionCategory.COMBAT,
            },
            Position.SITTING: {
                *aware,
                ActionCategory.MANIPULATE,
                ActionCategory.CHANGE_POSITION,
            },
            Position.RESTING: {
                *aware,
                ActionCategory.MANIPULATE,
                ActionCategory.CHANGE_POSITION,
            },
            Position.SLEEPING: {
                ActionCategory.STATE_INDEPENDENT,
                ActionCategory.WAKE,
            },
            Position.FIGHTING: {*aware, ActionCategory.COMBAT},
            Position.STUNNED: independent,
            Position.INCAPACITATED: independent,
            Position.DYING: independent,
            Position.DEAD: independent,
        }

        for position in Position:
            allowed = {
                action for action in ActionCategory if action_allowed(position, action)
            }
            self.assertEqual(allowed, expected[position], position.value)

    def test_checks_require_valid_enums(self):
        with self.assertRaises(ActionPolicyError):
            action_allowed("standing", ActionCategory.MOVE)
        with self.assertRaises(ActionPolicyError):
            self.char1.actions.check("move")


class TestEffectivePosition(EvenniaTest):
    """The strictest state wins without changing persistent posture."""

    def setUp(self):
        super().setUp()
        self.char1.db.position = "resting"

    def test_combined_state_precedence(self):
        imposed = [Position.FIGHTING, Position.STUNNED, Position.DYING]
        with patch.object(
            Character, "get_imposed_action_positions", return_value=imposed
        ):
            resolution = resolve_position(self.char1)

        self.assertEqual(resolution.position, Position.DYING)
        self.assertEqual(resolution.posture, Position.RESTING)
        self.assertEqual(self.char1.db.position, "resting")

    def test_missing_posture_uses_legacy_standing_default(self):
        self.char1.attributes.remove("position")

        resolution = resolve_position(self.char1)

        self.assertTrue(resolution.valid)
        self.assertEqual(resolution.position, Position.STANDING)

    def test_invalid_posture_fails_closed_but_allows_recovery(self):
        self.char1.db.position = "levitating"

        blocked = self.char1.actions.check(ActionCategory.MANIPULATE)
        recovery = self.char1.actions.check(ActionCategory.STATE_INDEPENDENT)

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.position, Position.INCAPACITATED)
        self.assertIn("contact staff", blocked.message)
        self.assertTrue(recovery.allowed)

    def test_invalid_imposed_position_fails_closed(self):
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=["not-a-position"],
        ):
            decision = self.char1.actions.check(ActionCategory.OBSERVE)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.position, Position.INCAPACITATED)

    def test_temporary_stun_restores_underlying_posture_on_expiry(self):
        self.char1.effects.add(_STUN.key, quiet=True)

        self.assertEqual(self.char1.action_position, Position.STUNNED)
        self.assertFalse(self.char1.actions.check(ActionCategory.OBSERVE).allowed)

        self.char1.effects.process_duration(1, quiet=True)

        self.assertEqual(self.char1.action_position, Position.RESTING)
        self.assertEqual(self.char1.db.position, "resting")
        self.assertTrue(self.char1.actions.check(ActionCategory.OBSERVE).allowed)


class TestPostureTransitions(EvenniaTest):
    """The handler owns all voluntary posture transitions."""

    def test_valid_change_and_idempotent_repeat(self):
        changed = self.char1.actions.transition(Position.SITTING)
        repeated = self.char1.actions.transition(Position.SITTING)

        self.assertEqual(changed.outcome, TransitionOutcome.CHANGED)
        self.assertEqual(repeated.outcome, TransitionOutcome.ALREADY)
        self.assertEqual(self.char1.db.position, "sitting")

    def test_sleep_requires_explicit_wake_transition(self):
        self.char1.db.position = "sleeping"

        denied = self.char1.actions.transition(Position.STANDING)
        woke = self.char1.actions.transition(
            Position.SITTING, action=ActionCategory.WAKE
        )

        self.assertEqual(denied.outcome, TransitionOutcome.DENIED)
        self.assertEqual(woke.outcome, TransitionOutcome.CHANGED)
        self.assertEqual(self.char1.db.position, "sitting")

    def test_imposed_state_blocks_posture_change(self):
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=[Position.FIGHTING],
        ):
            result = self.char1.actions.transition(Position.SITTING)

        self.assertEqual(result.outcome, TransitionOutcome.DENIED)
        self.assertEqual(self.char1.db.position, "standing")

    def test_rejects_non_posture_target(self):
        with self.assertRaises(ActionPolicyError):
            self.char1.actions.transition(Position.FIGHTING)


class TestMovementPolicy(EvenniaTest):
    """Traversal is guarded for PCs and NPCs without blocking forced moves."""

    def setUp(self):
        super().setUp()
        self.char1.msg = MagicMock()

    def test_direct_traversal_requires_standing_and_denies_once(self):
        self.char1.db.position = "resting"

        moved = self.char1.move_to(self.room2, quiet=True, move_type="traverse")

        self.assertFalse(moved)
        self.assertEqual(self.char1.location, self.room1)
        self.char1.msg.assert_called_once_with(
            "You need to stand before you can do that."
        )

    def test_standing_character_can_traverse(self):
        moved = self.char1.move_to(self.room2, quiet=True, move_type="traverse")

        self.assertTrue(moved)
        self.assertEqual(self.char1.location, self.room2)

    def test_explicit_non_traversal_relocation_bypasses_policy(self):
        self.char1.db.position = "sleeping"

        moved = self.char1.move_to(self.room2, quiet=True, move_type="teleport")

        self.assertTrue(moved)
        self.assertEqual(self.char1.location, self.room2)

    def test_npc_uses_the_same_traversal_policy(self):
        npc = create_object(
            "typeclasses.characters.Character",
            key="Test NPC",
            location=self.room1,
        )
        npc.db.position = "sitting"
        npc.msg = MagicMock()

        moved = npc.move_to(self.room2, quiet=True, move_type="traverse")

        self.assertFalse(moved)
        self.assertEqual(npc.location, self.room1)
        npc.msg.assert_called_once_with("You need to stand before you can do that.")

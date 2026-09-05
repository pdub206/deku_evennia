"""Regression coverage for COMBAT-01's persistent encounter registry."""

from unittest.mock import patch

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.action_policy import ActionCategory, Position
from systems.combat import (
    COMBAT_CONFIG_KEY,
    CombatActionResult,
    change_target,
    get_target,
    is_fighting,
    join_fight,
    leave_fight,
    process_combat_pulse,
    set_combat_action_hook,
    start_fight,
)
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    UnavailabilityCause,
)
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character


class TestCombatRegistry(EvenniaTest):
    """Combat membership is unique, room-scoped, and self-repairing."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        set_combat_action_hook(None)
        self.addCleanup(set_combat_action_hook, None)
        self.char3 = create_object(Character, key="Char3", location=self.room1)

    def test_start_join_retarget_leave_and_dissolution_are_idempotent(self):
        started = start_fight(self.char1, self.char2)
        duplicate = start_fight(self.char1, self.char2)
        joined = join_fight(self.char3, self.char2)

        self.assertTrue(started.accepted)
        self.assertTrue(started.changed)
        self.assertFalse(duplicate.changed)
        self.assertTrue(joined.changed)
        self.assertTrue(is_fighting(self.char1))
        self.assertTrue(is_fighting(self.char3))
        self.assertEqual(self.char1.action_position, Position.FIGHTING)
        self.assertTrue(self.char1.actions.check(ActionCategory.COMBAT).allowed)
        self.assertFalse(self.char1.actions.check(ActionCategory.MOVE).allowed)
        self.assertFalse(
            self.char1.actions.check(ActionCategory.CHANGE_POSITION).allowed
        )

        retargeted = change_target(self.char1, self.char3)
        self.assertTrue(retargeted.changed)
        self.assertIs(get_target(self.char1), self.char3)

        left = leave_fight(self.char3)
        self.assertTrue(left.changed)
        self.assertIs(get_target(self.char1), self.char2)
        self.assertTrue(leave_fight(self.char3).accepted)
        self.assertFalse(leave_fight(self.char3).changed)

        leave_fight(self.char1)
        self.assertFalse(is_fighting(self.char1))
        self.assertFalse(is_fighting(self.char2))
        self.assertEqual(self.char1.action_position, Position.STANDING)

    def test_rejects_self_cross_room_and_noncharacter_targets(self):
        self.assertFalse(start_fight(self.char1, self.char1).accepted)
        self.char2.move_to(self.room2, quiet=True, move_type="teleport")
        self.assertFalse(start_fight(self.char1, self.char2).accepted)
        self.assertFalse(start_fight(self.char1, self.room1).accepted)
        self.assertFalse(is_fighting(self.char1))

    def test_movement_and_final_lifecycle_event_remove_membership(self):
        start_fight(self.char1, self.char2)
        self.char1.move_to(self.room2, quiet=True, move_type="teleport")
        self.assertFalse(is_fighting(self.char1))
        self.assertFalse(is_fighting(self.char2))

        self.char1.move_to(self.room1, quiet=True, move_type="teleport")
        start_fight(self.char1, self.char2)
        from systems.combat import _on_character_lifecycle

        _on_character_lifecycle(
            CharacterLifecycleEvent(
                self.char1,
                CharacterAvailability.UNAVAILABLE,
                1,
                UnavailabilityCause.OOC,
            )
        )
        self.assertFalse(is_fighting(self.char1))
        self.assertFalse(is_fighting(self.char2))

    def test_malformed_state_fails_closed_and_is_repaired(self):
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, value={"bad": "state"})
        with patch("systems.combat.logger.log_err") as log_error:
            result = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))

        self.assertTrue(result.processed)
        self.assertFalse(is_fighting(self.char1))
        self.assertEqual(ServerConfig.objects.conf(COMBAT_CONFIG_KEY)["encounters"], {})
        log_error.assert_called_once()


class TestCombatPulse(EvenniaTest):
    """Readiness clocks and pulse idempotency protect action execution."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        set_combat_action_hook(None)
        self.addCleanup(set_combat_action_hook, None)

    def test_reaction_order_and_replayed_token_execute_once(self):
        self.char1.db.reaction_modifier_override = 5
        self.char2.db.reaction_modifier_override = -5
        start_fight(self.char2, self.char1)
        calls = []

        def action(actor, target, event):
            calls.append((actor.id, target.id, event.sequence))
            return CombatActionResult(acted=True)

        set_combat_action_hook(action)
        event = PulseEvent(2, PulseLane.COMBAT, 1)
        first = process_combat_pulse(event)
        replay = process_combat_pulse(event)
        second = process_combat_pulse(PulseEvent(4, PulseLane.COMBAT, 2))

        self.assertEqual(
            [call[0] for call in calls],
            [self.char1.id, self.char2.id, self.char1.id],
        )
        self.assertEqual(first.actions, 2)
        self.assertFalse(replay.processed)
        self.assertEqual(second.actions, 1)

    def test_action_revalidation_and_failure_isolation(self):
        char3 = create_object(Character, key="Char3", location=self.room1)
        start_fight(self.char1, self.char2)
        join_fight(char3, self.char2)
        calls = []

        def action(actor, target, event):
            calls.append(actor.id)
            if actor is self.char1:
                char3.move_to(self.room2, quiet=True, move_type="teleport")
            if actor is self.char2:
                raise RuntimeError("isolated resolver failure")
            return CombatActionResult()

        set_combat_action_hook(action)
        result = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))

        self.assertIn(self.char1.id, calls)
        self.assertIn(self.char2.id, calls)
        self.assertNotIn(char3.id, calls)
        self.assertEqual(result.actions, 1)
        self.assertEqual(result.failures, 1)

"""INTERACT-06 durable action queue coverage."""

from unittest.mock import Mock

from commands.generic import CmdActionQueue
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.action_queue import (
    ACTION_ATTRIBUTE,
    ActionDefinition,
    ActionQueueError,
    ActionStatus,
    action_audit,
    cancel_action,
    current_action_sequence,
    inspect_action,
    process_action_pulse,
    register_action_definition,
    schedule_action,
    unregister_action_definition,
)
from systems.lifecycle import (
    ServerLifecycleEvent,
    ServerTransitionMode,
    ServerTransitionPhase,
    UnavailabilityCause,
    mark_character_unavailable,
)
from systems.pulses import PulseEvent, PulseLane


class TestActionQueue(EvenniaTest):
    """The queue persists primitives and consumes each due identity once."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.executed = Mock()
        self.released = Mock()
        self.committed = Mock()
        self.keys = []

    def tearDown(self):
        for key in self.keys:
            unregister_action_definition(key)
        super().tearDown()

    def definition(self, suffix="basic", **overrides):
        def execute(owner, args, reservation):
            self.executed(owner, args, reservation)

        key = f"test.interact06.{suffix}"
        values = {
            "key": key,
            "conflict_group": "movement",
            "validate": lambda owner, args: True,
            "execute": execute,
        }
        values.update(overrides)
        definition = ActionDefinition(**values)
        register_action_definition(definition)
        self.keys.append(key)
        return definition

    def pulse(self, sequence):
        return process_action_pulse(PulseEvent(sequence, PulseLane.ACTIONS, sequence))

    def test_schedule_wait_execute_and_duplicate_delivery(self):
        definition = self.definition()
        result = schedule_action(self.char1, definition.key, {"target_id": 7}, delay=2)
        self.assertEqual(result.status, ActionStatus.QUEUED)
        self.assertEqual(inspect_action(self.char1)["arguments"], {"target_id": 7})
        self.pulse(1)
        self.executed.assert_not_called()
        self.pulse(2)
        self.pulse(2)
        self.executed.assert_called_once()
        self.assertIsNone(inspect_action(self.char1))
        self.assertEqual(action_audit(self.char1)[-1]["status"], "completed")

    def test_conflict_replacement_and_reservation_finalization(self):
        first = self.definition(
            "reserved",
            acquire=lambda owner, args: {"coins": 2},
            release=self.released,
            commit=self.committed,
        )
        second = self.definition("replacement", replace_existing=True)
        schedule_action(self.char1, first.key, {}, delay=2)
        denied = schedule_action(self.char1, "test.interact06.basic", {}, delay=1)
        self.assertEqual(denied.reason, "unknown_definition")
        replaced = schedule_action(self.char1, second.key, {}, delay=1)
        self.assertEqual(replaced.status, ActionStatus.REPLACED)
        self.released.assert_called_once_with(self.char1, {"coins": 2})
        self.pulse(1)
        self.assertEqual(action_audit(self.char1)[-1]["status"], "completed")

    def test_execution_revalidation_cancels_and_releases(self):
        allowed = {"value": True}
        definition = self.definition(
            "revalidate",
            validate=lambda owner, args: allowed["value"] or "state_changed",
            acquire=lambda owner, args: {"held": True},
            release=self.released,
            commit=self.committed,
        )
        schedule_action(self.char1, definition.key, {}, delay=1)
        allowed["value"] = False
        self.pulse(1)
        self.executed.assert_not_called()
        self.released.assert_called_once()
        self.committed.assert_not_called()
        self.assertEqual(action_audit(self.char1)[-1]["reason"], "state_changed")

    def test_disconnect_and_cold_restart_cancel_but_reload_can_survive(self):
        normal = self.definition("lifecycle")
        schedule_action(self.char1, normal.key, {}, delay=3)
        mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        self.assertIsNone(inspect_action(self.char1))

        from systems.action_queue import _on_server_lifecycle

        schedule_action(self.char1, normal.key, {}, delay=3)
        _on_server_lifecycle(
            ServerLifecycleEvent(
                ServerTransitionMode.HOT_RELOAD,
                ServerTransitionPhase.PREPARE,
                1,
            )
        )
        self.assertIsNotNone(inspect_action(self.char1))
        _on_server_lifecycle(
            ServerLifecycleEvent(
                ServerTransitionMode.COLD_RESTART,
                ServerTransitionPhase.PREPARE,
                2,
            )
        )
        self.assertIsNone(inspect_action(self.char1))

    def test_rejects_runtime_objects_and_quarantines_malformed_state(self):
        definition = self.definition("primitive")
        result = schedule_action(
            self.char1, definition.key, {"bad": self.room1}, delay=1
        )
        self.assertEqual(result.reason, "validation_failed")
        self.char1.attributes.add(ACTION_ATTRIBUTE, {"version": 1})
        with self.assertRaises(ActionQueueError):
            inspect_action(self.char1)
        self.pulse(1)
        self.assertEqual(self.char1.attributes.get(ACTION_ATTRIBUTE), {"version": 1})

    def test_explicit_cancel_is_idempotent(self):
        definition = self.definition("cancel")
        schedule_action(self.char1, definition.key, {}, delay=1)
        self.assertEqual(cancel_action(self.char1).status, ActionStatus.CANCELLED)
        self.assertEqual(cancel_action(self.char1).reason, "no_action")

    def test_record_owner_mismatch_is_quarantined_without_execution(self):
        definition = self.definition("wrong_owner")
        schedule_action(self.char1, definition.key, {}, delay=1)
        record = inspect_action(self.char1)
        record["owner_id"] = self.char2.id
        self.char1.attributes.add(ACTION_ATTRIBUTE, record)

        self.pulse(1)

        self.executed.assert_not_called()
        with self.assertRaises(ActionQueueError):
            inspect_action(self.char1)

    def test_out_of_order_pulse_does_not_rewind_action_clock(self):
        self.pulse(5)
        self.pulse(3)

        self.assertEqual(current_action_sequence(), 5)

    def test_reservation_acquisition_failure_leaves_no_action(self):
        definition = self.definition(
            "reservation_failure",
            acquire=Mock(side_effect=RuntimeError("unavailable")),
            release=self.released,
            commit=self.committed,
        )

        result = schedule_action(self.char1, definition.key, {}, delay=1)

        self.assertEqual(result.status, ActionStatus.DECLINED)
        self.assertEqual(result.reason, "reservation_failed")
        self.assertIsNone(inspect_action(self.char1))
        self.released.assert_not_called()
        self.committed.assert_not_called()


class TestActionQueueCommand(EvenniaCommandTest):
    """Builder diagnostics expose bounded state and safe operations."""

    definition_key = "test.interact06.diagnostics"

    def setUp(self):
        super().setUp()
        register_action_definition(
            ActionDefinition(
                self.definition_key,
                "movement",
                lambda owner, args: True,
                lambda owner, args, reservation: None,
            )
        )
        self.addCleanup(unregister_action_definition, self.definition_key)

    def test_builder_diagnostics_inspect_and_cancel(self):
        schedule_action(self.char1, self.definition_key, {}, delay=2)
        self.call(
            CmdActionQueue(),
            self.char1.key,
            f"{self.char1.key}: {self.definition_key} [queued] due 2; audit entries: 0.",
        )
        self.call(
            CmdActionQueue(),
            f"/cancel {self.char1.key}",
            "Action queue: cancelled (staff_cancel).",
        )

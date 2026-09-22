"""AREA-03A controller scheduling and persistence coverage."""

from unittest.mock import Mock

from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.area_resets import (
    AREA_RESET_CONFIG_KEY,
    controller_snapshot,
    process_area_reset_plan,
    request_manual_reset,
)
from systems.areas import assign_area, compile_area_load_plan
from systems.pulses import PulseEvent, PulseLane


class TestAreaResetControllers(EvenniaTest):
    """One global lane owns durable policy, due, and failure state."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.filter(db_key=AREA_RESET_CONFIG_KEY).delete()
        assign_area(self.room1, "alpha")
        self.char2.location = None

    @staticmethod
    def _manifest(policy="always", lifespan=2):
        return {
            "key": "alpha",
            "display_name": "Alpha",
            "schema_version": 1,
            "dependencies": [],
            "credits": [],
            "srd_references": [],
            "reset_policy": policy,
            "lifespan_pulses": lifespan,
            "rooms": {
                "entry": {
                    "name": "Entry",
                    "description": "",
                    "extra_descriptions": [],
                    "sector": "inside",
                    "policy": {},
                    "environment": {},
                    "weather_profile": None,
                }
            },
            "exits": {},
            "mobiles": [],
            "objects": {},
        }

    def _plan(self, policy="always", lifespan=2):
        return compile_area_load_plan(
            manifests={"alpha": self._manifest(policy, lifespan)}
        )

    @staticmethod
    def _event(sequence):
        return PulseEvent(sequence, PulseLane.RESETS, sequence)

    def test_always_is_due_then_waits_and_warns_occupants_once(self):
        runner = Mock()
        self.char1.db.is_player_character = True
        self.char1.location = self.room1
        self.char1.msg = Mock()

        first = process_area_reset_plan(
            self._event(0), self._plan(), directive_runner=runner
        )
        waiting = process_area_reset_plan(
            self._event(1), self._plan(), directive_runner=runner
        )
        due = process_area_reset_plan(
            self._event(2), self._plan(), directive_runner=runner
        )

        self.assertEqual([result.status for result in first], ["completed"])
        self.assertEqual([result.reason for result in waiting], ["not_due"])
        self.assertEqual([result.status for result in due], ["completed"])
        self.assertEqual(runner.call_count, 2)
        self.char1.msg.assert_called_once_with("The area shifts uneasily around you.")
        self.assertEqual(controller_snapshot()["alpha"]["last_completed_token"], 2)

    def test_if_empty_stays_due_until_physical_pc_leaves(self):
        runner = Mock()
        self.char1.db.is_player_character = True
        self.char1.location = self.room1
        process_area_reset_plan(
            self._event(0), self._plan("if_empty"), directive_runner=runner
        )

        skipped = process_area_reset_plan(
            self._event(1), self._plan("if_empty"), directive_runner=runner
        )
        self.char1.location = None
        completed = process_area_reset_plan(
            self._event(2), self._plan("if_empty"), directive_runner=runner
        )

        self.assertEqual(skipped[0].reason, "occupied")
        self.assertEqual(
            completed[0].status, "completed", controller_snapshot()["alpha"]
        )
        self.assertEqual(runner.call_count, 1)

    def test_boot_revision_and_never_manual_only(self):
        runner = Mock()
        plan = self._plan("boot")
        process_area_reset_plan(self._event(0), plan, directive_runner=runner)
        process_area_reset_plan(self._event(1), plan, directive_runner=runner)
        revised = self._plan("boot", lifespan=3)
        process_area_reset_plan(self._event(2), revised, directive_runner=runner)
        never = self._plan("never")
        idle = process_area_reset_plan(self._event(3), never, directive_runner=runner)
        manual = request_manual_reset("alpha", never, directive_runner=runner)

        self.assertEqual(runner.call_count, 3)
        self.assertEqual(idle[0].reason, "manual_only")
        self.assertEqual(manual.status, "completed")

    def test_failure_isolated_and_replayed_token_is_not_run_twice(self):
        plan = self._plan()

        failed = process_area_reset_plan(
            self._event(0),
            plan,
            directive_runner=Mock(side_effect=RuntimeError("boom")),
        )
        runner = Mock()
        completed = process_area_reset_plan(
            self._event(1), plan, directive_runner=runner
        )
        duplicate = process_area_reset_plan(
            self._event(1), plan, directive_runner=runner
        )

        self.assertEqual(failed[0].status, "failed")
        self.assertEqual(completed[0].status, "completed")
        self.assertEqual(duplicate[0].status, "duplicate")
        self.assertEqual(runner.call_count, 1)

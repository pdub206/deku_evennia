"""Regression tests for MOB-01's durable mobile behavior runner."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.combat import start_fight
from systems.mobiles import (
    MOBILE_BEHAVIOR_ATTRIBUTE,
    MobileAction,
    MobileActionDefinition,
    MobileBehaviorDefinition,
    initial_mobile_state,
    mobile_state,
    pause_mobile,
    process_mobile_pulse,
    register_action,
    register_behavior,
    resume_mobile,
    set_mobile_profile,
)
from systems.pulses import PulseEvent, PulseLane
from typeclasses.scripts import GamePulseScript


def _select_mark(npc, event, config):
    """Select a deterministic test action with a deliberate two-token delay."""
    return MobileAction("test.mobiles.mark", {"token": event.sequence}, 2)


def _execute_mark(npc, event, data):
    """Record test execution outside the persisted behavior payload."""
    npc.ndb.mobile_marks = (getattr(npc.ndb, "mobile_marks", None) or []) + [
        data["token"]
    ]


try:
    register_action(MobileActionDefinition("test.mobiles.mark", _execute_mark))
    register_behavior(MobileBehaviorDefinition("test.mobiles.marking", _select_mark))
except ValueError:
    # Test modules can be imported more than once by Evennia's reload tooling.
    pass


def _event(sequence: int) -> PulseEvent:
    """Make a valid mobile lane event with an unimportant heartbeat."""
    return PulseEvent(sequence * 10, PulseLane.MOBILES, sequence)


class TestMobileBehaviorPulse(EvenniaTest):
    """NPCs alone consume one durable decision token per due mobile pulse."""

    script_typeclass = GamePulseScript

    def npc(self, key="Mobile"):
        """Create an in-world unpuppeted NPC marked by the durable NPC flag."""
        npc = create_object(
            "typeclasses.characters.Character", key=key, location=self.room1
        )
        npc.db.is_player_character = False
        return npc

    def test_lane_dispatches_the_project_mobile_service(self):
        event = _event(1)
        with patch("systems.mobiles.process_mobile_pulse") as process:
            self.script.at_mobiles_pulse(event)
        process.assert_called_once_with(event)

    def test_pc_is_excluded_and_default_npc_profile_is_idle(self):
        self.char1.db.is_player_character = True
        npc = self.npc()

        result = process_mobile_pulse(_event(1))

        self.assertEqual(len(result.outcomes), 1)
        self.assertEqual(
            (result.outcomes[0].status, result.outcomes[0].reason), ("idle", "idle")
        )
        self.assertEqual(mobile_state(npc)["behavior_key"], "idle")
        self.assertIsNone(self.char1.attributes.get(MOBILE_BEHAVIOR_ATTRIBUTE))

    def test_multiple_npcs_act_independently_with_per_npc_delay(self):
        first, second = self.npc("First"), self.npc("Second")
        set_mobile_profile(first, "test.mobiles.marking")
        set_mobile_profile(second, "test.mobiles.marking")

        first_result = process_mobile_pulse(_event(1))
        delayed = process_mobile_pulse(_event(2))
        second_result = process_mobile_pulse(_event(3))

        self.assertEqual(first_result.acted, 2)
        self.assertTrue(
            all(outcome.reason == "not_due" for outcome in delayed.outcomes)
        )
        self.assertEqual(second_result.acted, 2)
        self.assertEqual(first.ndb.mobile_marks, [1, 3])
        self.assertEqual(second.ndb.mobile_marks, [1, 3])
        self.assertEqual(mobile_state(first)["action_data"], {"token": 3})

    def test_duplicate_token_is_idempotent_after_selection_and_execution(self):
        npc = self.npc()
        set_mobile_profile(npc, "test.mobiles.marking")

        process_mobile_pulse(_event(1))
        repeated = process_mobile_pulse(_event(1))

        self.assertEqual(npc.ndb.mobile_marks, [1])
        self.assertEqual(repeated.outcomes[0].reason, "duplicate_token")

    def test_pause_reload_and_resume_never_catch_up_missed_actions(self):
        npc = self.npc()
        set_mobile_profile(npc, "test.mobiles.marking")
        process_mobile_pulse(_event(1))
        pause_mobile(npc, "builder maintenance")

        paused = process_mobile_pulse(_event(2))
        reloaded = type(npc).objects.get(pk=npc.pk)
        resume_mobile(reloaded, next_token=4)
        process_mobile_pulse(_event(3))
        resumed = process_mobile_pulse(_event(4))

        self.assertEqual(paused.outcomes[0].status, "paused")
        self.assertEqual(reloaded.ndb.mobile_marks, [1, 4])
        self.assertEqual(resumed.acted, 1)

    def test_dead_off_grid_and_fighting_npcs_fail_closed(self):
        dead, off_grid, fighting = (
            self.npc("Dead"),
            self.npc("Off grid"),
            self.npc("Fighting"),
        )
        dead.db.hp_current = 0
        dead.db.injury_state = {
            "version": 1,
            "state": "dead",
            "successes": 0,
            "failures": 0,
            "last_recovery": 0,
            "death_id": "test-mobile-death",
        }
        off_grid.location = None
        start_fight(self.char1, fighting)

        result = process_mobile_pulse(_event(1), mobiles=(dead, off_grid, fighting))

        self.assertEqual(
            [outcome.reason for outcome in result.outcomes],
            ["incapacitated", "off_grid", "fighting"],
        )
        self.assertTrue(
            all(
                obj.attributes.get(MOBILE_BEHAVIOR_ATTRIBUTE) is None
                for obj in (dead, off_grid, fighting)
            )
        )

    def test_malformed_and_unknown_state_fail_without_executing(self):
        malformed, unknown = self.npc("Malformed"), self.npc("Unknown")
        malformed.attributes.add(MOBILE_BEHAVIOR_ATTRIBUTE, {"version": 99})
        unknown.attributes.add(
            MOBILE_BEHAVIOR_ATTRIBUTE,
            {**initial_mobile_state(), "behavior_key": "gone"},
        )

        result = process_mobile_pulse(_event(1))

        reasons = {outcome.npc_id: outcome.reason for outcome in result.outcomes}
        self.assertEqual(reasons[malformed.id], "malformed_state")
        self.assertEqual(reasons[unknown.id], "unknown_behavior")
        self.assertEqual(mobile_state(unknown)["last_consumed_token"], 1)

    def test_one_npc_selector_failure_does_not_block_other_npcs(self):
        broken, valid = self.npc("Broken"), self.npc("Valid")
        set_mobile_profile(broken, "test.mobiles.marking")
        set_mobile_profile(valid, "test.mobiles.marking")

        def selector(npc, event, config):
            if npc is broken:
                raise RuntimeError("test failure")
            return _select_mark(npc, event, config)

        result = process_mobile_pulse(_event(1), select_action=selector)

        self.assertEqual(result.outcomes[0].reason, "selection_failed")
        self.assertEqual(result.outcomes[1].status, "acted")
        self.assertEqual(valid.ndb.mobile_marks, [1])

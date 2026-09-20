"""INTERACT-03 sector, cost, queued traversal, and mobile cadence coverage."""

from unittest.mock import MagicMock, patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.action_queue import action_audit, inspect_action, process_action_pulse
from systems.doors import configure_door
from systems.lifecycle import (
    ServerLifecycleEvent,
    ServerTransitionMode,
    ServerTransitionPhase,
)
from systems.mobile_navigation import NavigationRequest, execute_navigation
from systems.mobiles import mobile_state
from systems.pulses import PulseEvent, PulseLane
from systems.travel import SECTORS, sector_key, travel_decision
from typeclasses.characters import Character
from typeclasses.exits import Exit


class TestTravel(EvenniaTest):
    """All traversal consumers share destination terrain and speed rules."""

    def setUp(self):
        super().setUp()
        self.exit = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )

    def test_registry_is_complete_and_costs_scale_with_speed(self):
        self.assertEqual(
            set(SECTORS),
            {
                "inside",
                "city",
                "field",
                "forest",
                "hills",
                "mountain",
                "shallow_water",
                "deep_water",
                "air",
            },
        )
        self.room2.db.sector = "mountain"
        self.char1.db.speed = 30
        self.assertEqual(travel_decision(self.char1, self.exit).delay, 6)
        self.char1.db.speed = 60
        self.assertEqual(travel_decision(self.char1, self.exit).delay, 3)
        self.char1.db.speed = 15
        self.assertEqual(travel_decision(self.char1, self.exit).delay, 12)
        self.char1.db.speed = 0
        self.assertEqual(travel_decision(self.char1, self.exit).reason, "immobilized")

    def test_every_sector_uses_its_registered_cost(self):
        boat = create_object(
            "typeclasses.objects.Object", key="boat", location=self.char1
        )
        boat.db.type = "boat"
        boat.db.equipment_capabilities = ["terrain:boat"]
        self.char1.db.speed = 30
        with patch("systems.travel._has_condition", return_value=True):
            for key, sector in SECTORS.items():
                with self.subTest(sector=key):
                    self.room2.db.sector = key
                    self.assertEqual(
                        travel_decision(self.char1, self.exit).delay, sector.cost
                    )

    def test_absent_sector_defaults_and_malformed_values_fail_closed(self):
        self.assertEqual(sector_key(self.room2), "inside")
        self.room2.db.sector = "bogus"
        self.assertEqual(
            travel_decision(self.char1, self.exit).reason, "invalid_sector"
        )

    def test_weather_is_multiplied_and_bounded(self):
        self.room2.db.sector = "field"
        with patch("systems.travel.weather_travel_multiplier", return_value=1.5):
            self.assertEqual(travel_decision(self.char1, self.exit).delay, 3)
        with patch("systems.travel.weather_travel_multiplier", return_value=5):
            self.assertEqual(
                travel_decision(self.char1, self.exit).reason, "invalid_weather"
            )

    def test_deep_water_needs_direct_functional_boat(self):
        self.room2.db.sector = "deep_water"
        self.assertEqual(travel_decision(self.char1, self.exit).reason, "boat_or_swim")
        boat = create_object(
            "typeclasses.objects.Object", key="boat", location=self.char1
        )
        boat.db.type = "boat"
        boat.db.equipment_capabilities = ["terrain:boat"]
        self.assertTrue(travel_decision(self.char1, self.exit).allowed)
        boat.db.broken = True
        self.assertEqual(travel_decision(self.char1, self.exit).reason, "boat_or_swim")

    def test_registered_swim_and_flight_condition_hooks(self):
        with patch(
            "systems.travel._has_condition",
            side_effect=lambda actor, key: key in {"swim", "flight"},
        ):
            self.room2.db.sector = "deep_water"
            self.assertTrue(travel_decision(self.char1, self.exit).allowed)
            self.room2.db.sector = "air"
            self.assertTrue(travel_decision(self.char1, self.exit).allowed)

    def test_pc_exit_queues_and_direct_traversal_is_denied(self):
        self.exit.at_traverse(self.char1, self.room2)
        self.assertIs(self.char1.location, self.room1)
        record = inspect_action(self.char1)
        self.assertEqual(record["definition"], "interact03.travel")
        self.assertFalse(self.char1.move_to(self.room2, move_type="traverse"))

    def test_due_pc_travel_moves_exactly_once(self):
        self.exit.at_traverse(self.char1, self.room2)
        due = inspect_action(self.char1)["due_token"]

        process_action_pulse(PulseEvent(due, PulseLane.ACTIONS, due))
        process_action_pulse(PulseEvent(due, PulseLane.ACTIONS, due))

        self.assertIs(self.char1.location, self.room2)
        self.assertIsNone(inspect_action(self.char1))

    def test_execution_change_cancels_once_with_safe_message(self):
        self.exit.at_traverse(self.char1, self.room2)
        due = inspect_action(self.char1)["due_token"]
        configure_door(self.exit, initial_state="closed")
        self.char1.msg = MagicMock()

        process_action_pulse(PulseEvent(due, PulseLane.ACTIONS, due))
        process_action_pulse(PulseEvent(due, PulseLane.ACTIONS, due))

        self.assertIs(self.char1.location, self.room1)
        self.char1.msg.assert_called_once_with("You cannot travel that way right now.")
        self.assertEqual(action_audit(self.char1)[-1]["reason"], "exit_blocked")

    def test_hot_reload_preserves_and_cold_restart_cancels_travel(self):
        from systems.action_queue import _on_server_lifecycle

        self.exit.at_traverse(self.char1, self.room2)
        _on_server_lifecycle(
            ServerLifecycleEvent(
                ServerTransitionMode.HOT_RELOAD, ServerTransitionPhase.PREPARE, 1
            )
        )
        self.assertIsNotNone(inspect_action(self.char1))
        _on_server_lifecycle(
            ServerLifecycleEvent(
                ServerTransitionMode.COLD_RESTART, ServerTransitionPhase.PREPARE, 2
            )
        )
        self.assertIsNone(inspect_action(self.char1))

    def test_mobile_moves_once_and_adopts_travel_cadence(self):
        npc = create_object(Character, key="walker", location=self.room1)
        npc.db.is_player_character = False
        self.room2.db.sector = "forest"
        result = execute_navigation(NavigationRequest("wander", npc, 1), self.exit)
        self.assertEqual(result.status, "moved")
        self.assertEqual(mobile_state(npc)["next_eligible_token"], 4)

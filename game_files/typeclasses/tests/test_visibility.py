"""INTERACT-04 visibility, discovery, and inspection coverage."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.doors import configure_door_field
from systems.room_environment import set_room_environment_value
from systems.visibility import (Visibility, active_search, room_visibility,
                                target_visibility, validate_extra_descriptions)


class TestVisibility(EvenniaTest):
    """Canonical decisions compose light, senses, locks, and hidden state."""

    def test_dark_room_needs_light_or_current_room_darkvision(self):
        set_room_environment_value(self.room1, "light", "dark")

        self.assertEqual(
            room_visibility(self.char1, self.room1).outcome, Visibility.OBSCURED
        )
        self.char1.db.senses = ["darkvision"]
        self.assertTrue(room_visibility(self.char1, self.room1).visible)
        set_room_environment_value(self.room2, "light", "dark")
        self.assertFalse(room_visibility(self.char1, self.room2, adjacent=True).visible)

    def test_passive_and_active_hidden_exit_discovery_are_observer_specific(self):
        hidden_exit = create_object(
            "typeclasses.exits.Exit",
            key="crack",
            location=self.room1,
            destination=self.room2,
        )
        configure_door_field(hidden_exit, "door", "on")
        configure_door_field(hidden_exit, "hidden", "on")
        configure_door_field(hidden_exit, "discovery_dc", 30)

        self.assertFalse(target_visibility(self.char1, hidden_exit).visible)
        with patch("systems.visibility.resolve_check") as check:
            check.return_value.total = 30
            found = active_search(self.char1, [hidden_exit], roller=lambda _: 20)

        self.assertEqual(found, (hidden_exit,))
        self.assertTrue(target_visibility(self.char1, hidden_exit).visible)
        self.assertFalse(target_visibility(self.char2, hidden_exit).visible)

        self.char1.move_to(self.room2, move_type="forced")
        self.char1.move_to(self.room1, move_type="forced")
        self.assertFalse(target_visibility(self.char1, hidden_exit).visible)

    def test_malformed_environment_fails_closed(self):
        self.room1.db.room_environment = {"bad": "data"}

        decision = room_visibility(self.char1, self.room1)

        self.assertEqual(decision.outcome, Visibility.OBSCURED)
        self.assertEqual(decision.reason, "invalid_environment")

    def test_extra_description_validation_preserves_order_and_rejects_duplicates(self):
        records = validate_extra_descriptions(
            [
                {"keywords": ["mural"], "description": "A faded mural."},
                {
                    "keywords": ["crack", "fissure"],
                    "description": "A narrow crack.",
                    "discovery_dc": 12,
                },
            ]
        )
        self.assertEqual(records[0]["keywords"], ["mural"])
        with self.assertRaises(ValueError):
            validate_extra_descriptions(
                [
                    {"keywords": ["Mural"], "description": "One."},
                    {"keywords": ["mural"], "description": "Two."},
                ]
            )

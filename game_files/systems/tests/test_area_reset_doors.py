"""AREA-03B reset integration coverage for managed door records."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.area_resets import reconcile_door_resets
from systems.areas import (
    AREA_TAG_CATEGORY,
    EXIT_KEY_CATEGORY,
    ROOM_KEY_CATEGORY,
    compile_area_load_plan,
)
from systems.doors import configure_door, door_area_data, door_state, transition_door
from typeclasses.exits import Exit


class TestAreaResetDoors(EvenniaTest):
    """AREA-03B restores only exact source-owned doors through INTERACT-01."""

    def setUp(self):
        super().setUp()
        self._tag_room(self.room1, "alpha", "entry")
        self._tag_room(self.room2, "beta", "gatehouse")
        self.outbound = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )
        self.inbound = create_object(
            Exit, key="south", location=self.room2, destination=self.room1
        )
        self.outbound.tags.add("north_gate", category=EXIT_KEY_CATEGORY)
        self.inbound.tags.add("south_gate", category=EXIT_KEY_CATEGORY)
        values = {
            "initial_state": "locked",
            "key_kind": "iron_gate",
            "pickable": True,
            "pick_dc": 12,
            "pair_key": "north_gate_pair",
        }
        configure_door(self.outbound, **values)
        configure_door(self.inbound, **values)

    @staticmethod
    def _tag_room(room, area, key):
        for old in room.tags.get(category=AREA_TAG_CATEGORY, return_list=True):
            room.tags.remove(old, category=AREA_TAG_CATEGORY)
        for old in room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True):
            room.tags.remove(old, category=ROOM_KEY_CATEGORY)
        room.tags.add(area, category=AREA_TAG_CATEGORY)
        room.tags.add(key, category=ROOM_KEY_CATEGORY)

    @staticmethod
    def _room(name):
        return {
            "name": name,
            "description": "",
            "extra_descriptions": [],
            "sector": "inside",
            "policy": {},
            "environment": {},
            "weather_profile": None,
        }

    def _plan(self, *, initial_state=None):
        outbound_door = door_area_data(self.outbound)
        inbound_door = door_area_data(self.inbound)
        if initial_state is not None:
            outbound_door["initial_state"] = initial_state
            inbound_door["initial_state"] = initial_state
        outbound = {
            "source_room": "entry",
            "name": "north",
            "description": "",
            "aliases": [],
            "destination": {
                "kind": "external",
                "area_key": "beta",
                "room_key": "gatehouse",
            },
            "door": outbound_door,
        }
        inbound = {
            "source_room": "gatehouse",
            "name": "south",
            "description": "",
            "aliases": [],
            "destination": {
                "kind": "external",
                "area_key": "alpha",
                "room_key": "entry",
            },
            "door": inbound_door,
        }
        manifests = {
            "alpha": {
                "key": "alpha",
                "display_name": "Alpha",
                "schema_version": 1,
                "dependencies": ["beta"],
                "credits": [],
                "srd_references": [],
                "reset_policy": "always",
                "lifespan_pulses": 1,
                "rooms": {"entry": self._room("Entry")},
                "exits": {"north_gate": outbound},
                "mobiles": [],
                "objects": {},
            },
            "beta": {
                "key": "beta",
                "display_name": "Beta",
                "schema_version": 1,
                "dependencies": ["alpha"],
                "credits": [],
                "srd_references": [],
                "reset_policy": "always",
                "lifespan_pulses": 1,
                "rooms": {"gatehouse": self._room("Gatehouse")},
                "exits": {"south_gate": inbound},
                "mobiles": [],
                "objects": {},
            },
        }
        return compile_area_load_plan(manifests=manifests)

    def test_pair_is_restored_once_across_areas_and_replay_is_a_no_op(self):
        transition_door(
            self.outbound, open=True, locked=False, state_id="area03b:test:open"
        )
        plan = self._plan()

        reconcile_door_resets("alpha", "area-reset:alpha:4", plan)
        reconcile_door_resets("beta", "area-reset:beta:4", plan)
        reconcile_door_resets("alpha", "area-reset:alpha:4", plan)

        self.assertTrue(door_state(self.outbound).locked)
        self.assertEqual(
            door_state(self.outbound).last_reset_id,
            "area-door-reset:4:north_gate_pair",
        )
        self.assertEqual(door_state(self.outbound), door_state(self.inbound))

    def test_active_travel_defers_without_changing_live_state(self):
        transition_door(
            self.outbound, open=True, locked=False, state_id="area03b:test:open"
        )
        plan = self._plan()

        with patch("systems.area_resets._exit_has_active_travel", return_value=True):
            reconcile_door_resets("alpha", "area-reset:alpha:5", plan)

        self.assertTrue(door_state(self.outbound).open)
        self.assertIsNone(door_state(self.outbound).last_reset_id)

    def test_malformed_or_missing_live_exit_isolated_without_mutation(self):
        transition_door(
            self.outbound, open=True, locked=False, state_id="area03b:test:open"
        )
        self.outbound.tags.remove("north_gate", category=EXIT_KEY_CATEGORY)

        reconcile_door_resets("alpha", "area-reset:alpha:6", self._plan())

        self.assertTrue(door_state(self.outbound).open)

    def test_changed_manifest_door_is_blocked_without_rewriting_live_state(self):
        transition_door(
            self.outbound, open=True, locked=False, state_id="area03b:test:open"
        )

        reconcile_door_resets(
            "alpha", "area-reset:alpha:7", self._plan(initial_state="open")
        )

        self.assertTrue(door_state(self.outbound).open)
        self.assertFalse(door_state(self.outbound).locked)
        self.assertIsNone(door_state(self.outbound).last_reset_id)

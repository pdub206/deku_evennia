"""INTERACT-01A canonical door-state and synchronization coverage."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems import doors
from systems.doors import (
    DOOR_STATE_ATTRIBUTE,
    DoorError,
    apply_door_area_data,
    configure_door,
    door_area_data,
    door_diagnostic,
    door_state,
    paired_exit,
    restore_initial_state,
    transition_door,
    traversal_decision,
    validate_area_exit_doors,
)
from typeclasses.exits import Exit


class TestDoorState(EvenniaTest):
    """One logical door is safe, primitive, paired, and idempotent."""

    def setUp(self):
        super().setUp()
        self.north = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )
        self.south = create_object(
            Exit, key="south", location=self.room2, destination=self.room1
        )

    def pair(self, **overrides):
        """Configure a reciprocal test pair through the public API."""
        values = {
            "initial_state": "closed",
            "key_kind": "iron_gate",
            "pickable": True,
            "pick_dc": 15,
            "hidden": False,
            "discovery_dc": None,
            "pair_key": "courtyard_gate",
            **overrides,
        }
        configure_door(self.north, **values)
        configure_door(self.south, **values)

    def test_passage_and_one_way_door_traversal(self):
        self.assertTrue(traversal_decision(self.north).allowed)

        configure_door(self.north, initial_state="closed")
        self.assertEqual(traversal_decision(self.north).reason, "closed")
        self.assertFalse(self.north.at_traverse(self.char1, self.room2))
        self.assertEqual(self.char1.location, self.room1)

        transition_door(
            self.north, open=True, locked=False, state_id="builder:test:open"
        )
        # Player traversal is scheduled by INTERACT-03. This door-state unit
        # test exercises its already-authorized execution path instead.
        self.north.at_traverse(self.char1, self.room2, travel_execution=True)
        self.assertEqual(self.char1.location, self.room2)

    def test_pair_configuration_and_transition_are_synchronized(self):
        self.pair()

        self.assertEqual(paired_exit(self.north), self.south)
        result = transition_door(
            self.north, open=True, locked=False, state_id="door:test:open"
        )
        duplicate = transition_door(
            self.south, open=True, locked=False, state_id="door:test:open"
        )

        self.assertEqual(result.status, "changed")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertTrue(door_state(self.north).open)
        self.assertEqual(door_state(self.north), door_state(self.south))
        with self.assertRaisesRegex(DoorError, "conflicts"):
            transition_door(
                self.north,
                open=False,
                locked=True,
                state_id="door:test:open",
            )

    def test_pair_failure_rolls_back_both_sides(self):
        self.pair()
        original = doors._write_state

        def fail_second(target, state):
            if target.id == self.south.id:
                raise RuntimeError("injected second-side failure")
            return original(target, state)

        with patch("systems.doors._write_state", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                transition_door(
                    self.north,
                    open=True,
                    locked=False,
                    state_id="door:test:rollback",
                )

        fresh_north = Exit.objects.get(id=self.north.id)
        fresh_south = Exit.objects.get(id=self.south.id)
        self.assertFalse(door_state(fresh_north).open)
        self.assertFalse(door_state(fresh_south).open)

    def test_missing_ambiguous_and_divergent_pairs_fail_closed(self):
        configure_door(self.north, pair_key="missing_gate")
        self.assertEqual(traversal_decision(self.north).reason, "invalid_door")

        configure_door(self.north, pair_key=None)
        self.pair()
        raw = dict(self.south.attributes.get(DOOR_STATE_ATTRIBUTE))
        raw["hidden"] = True
        raw["discovery_dc"] = 12
        self.south.attributes.add(DOOR_STATE_ATTRIBUTE, raw)
        self.assertEqual(traversal_decision(self.north).reason, "invalid_door")

        self.pair()
        duplicate = create_object(
            Exit, key="southwest", location=self.room2, destination=self.room1
        )
        configure_door(
            duplicate,
            **{
                "initial_state": "closed",
                "key_kind": "iron_gate",
                "pickable": True,
                "pick_dc": 15,
                "pair_key": "courtyard_gate",
            }
        )
        self.assertEqual(traversal_decision(self.north).reason, "invalid_door")
        self.assertEqual(door_diagnostic(self.north)["status"], "invalid")

    def test_malformed_and_impossible_records_are_quarantined_by_readers(self):
        self.north.attributes.add(DOOR_STATE_ATTRIBUTE, {"version": 1})
        self.assertEqual(traversal_decision(self.north).reason, "invalid_door")
        self.assertEqual(door_diagnostic(self.north)["status"], "invalid")

        self.north.attributes.add(
            DOOR_STATE_ATTRIBUTE,
            {
                "version": 1,
                "is_door": True,
                "open": True,
                "locked": True,
                "key_kind": None,
                "pickable": False,
                "pick_dc": None,
                "hidden": False,
                "discovery_dc": None,
                "pair_key": None,
                "initial_state": "closed",
                "last_reset_id": None,
                "last_state_id": None,
            },
        )
        with self.assertRaisesRegex(DoorError, "open door"):
            door_state(self.north)

    def test_reset_restores_initial_state_once(self):
        self.pair(initial_state="locked")
        transition_door(
            self.north, open=True, locked=False, state_id="door:test:opened"
        )

        restored = restore_initial_state(self.south, "reset:test:one")
        duplicate = restore_initial_state(self.north, "reset:test:one")

        self.assertEqual(restored.status, "restored")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertTrue(door_state(self.north).locked)
        self.assertFalse(door_state(self.south).open)

    def test_area_data_is_authored_only_and_reconstructs_fresh_state(self):
        self.pair(initial_state="locked", hidden=True, discovery_dc=18)
        transition_door(
            self.north, open=True, locked=False, state_id="door:test:temporary"
        )
        data = door_area_data(self.north)

        self.assertNotIn("last_state_id", data)
        self.assertNotIn("open", data)
        other = create_object(
            Exit, key="gate", location=self.room1, destination=self.room2
        )
        data["pair_key"] = None
        apply_door_area_data(other, data)
        rebuilt = door_state(other)
        self.assertTrue(rebuilt.locked)
        self.assertIsNone(rebuilt.last_state_id)

    def test_area_pair_validation_rejects_incomplete_or_divergent_pairs(self):
        self.pair()
        data = door_area_data(self.north)
        with self.assertRaisesRegex(DoorError, "exactly two"):
            validate_area_exit_doors([("one", "north", "two", {"door_state": data})])

        changed = dict(data)
        changed["initial_state"] = "open"
        with self.assertRaisesRegex(DoorError, "divergent"):
            validate_area_exit_doors(
                [
                    ("one", "north", "two", {"door_state": data}),
                    ("two", "south", "one", {"door_state": changed}),
                ]
            )

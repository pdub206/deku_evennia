"""Command coverage for INTERACT-01B door, key, and picking behavior."""

from commands.doors import CmdClose, CmdLock, CmdOpen, CmdPick, CmdUnlock
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.doors import configure_door, door_state
from typeclasses.exits import Exit
from typeclasses.objects import Item


class TestDoorCommands(EvenniaCommandTest):
    """Commands resolve locally and mutate canonical state exactly once."""

    def setUp(self):
        super().setUp()
        self.door = create_object(
            Exit,
            key="north",
            aliases=["n"],
            location=self.room1,
            destination=self.room2,
        )
        configure_door(
            self.door,
            initial_state="closed",
            key_kind="iron",
            pickable=True,
            pick_dc=10,
        )

    def item(self, key, item_type, **attrs):
        """Create a directly carried configured test item."""
        item = create_object(Item, key=key, location=self.char1)
        item.db.type = item_type
        for name, value in attrs.items():
            item.attributes.add(name, value)
        return item

    def test_open_close_and_direction_alias(self):
        self.call(CmdOpen(), "n", "You open north.")
        self.assertTrue(door_state(self.door).open)
        self.call(CmdClose(), "north", "You close north.")
        self.assertFalse(door_state(self.door).open)

    def test_lock_and_unlock_require_direct_matching_key(self):
        wrong = self.item("brass key", "key", key_kind="brass")
        self.call(CmdLock(), "north", "You do not have the right key.")
        wrong.db.key_kind = "iron"
        box = self.item("box", "container", capacity=100)
        wrong.move_to(box, quiet=True)
        self.call(CmdLock(), "north", "You do not have the right key.")
        wrong.move_to(self.char1, quiet=True)
        self.call(CmdLock(), "north", "You lock north.")
        self.call(CmdUnlock(), "north", "You unlock north.")
        self.assertFalse(door_state(self.door).locked)

    def test_pick_consumes_failure_and_unlocks_on_success(self):
        self.item("thieves' tools", "other", tool_kind="thieves_tools")
        self.item("iron key", "key", key_kind="iron")
        self.call(CmdLock(), "north", "You lock north.")
        self.char1.db.dexterity = 10
        with self.settings():
            # Patch at the service boundary to make the d20 deterministic.
            from unittest.mock import patch

            with patch("systems.door_actions.resolve_check") as resolve:
                from systems.checks import CheckResult, RollMode

                resolve.return_value = CheckResult(
                    "lock_picking",
                    "Dexterity",
                    None,
                    "thieves_tools",
                    RollMode.STRAIGHT,
                    1,
                    0,
                    0,
                    0,
                    1,
                    10,
                    None,
                    False,
                )
                self.call(CmdPick(), "north", "You fail to pick north.")
            self.assertTrue(door_state(self.door).locked)
            with patch("systems.door_actions.resolve_check") as resolve:
                resolve.return_value = CheckResult(
                    "lock_picking",
                    "Dexterity",
                    None,
                    "thieves_tools",
                    RollMode.STRAIGHT,
                    20,
                    0,
                    0,
                    0,
                    20,
                    10,
                    None,
                    True,
                )
                self.call(CmdPick(), "north", "You unlock north.")
        self.assertFalse(door_state(self.door).locked)

    def test_container_and_hidden_or_inaccessible_targets(self):
        chest = self.item("chest", "container")
        configure_door(chest, initial_state="closed")
        self.call(CmdOpen(), "chest", "You open chest.")
        self.assertTrue(door_state(chest).open)
        configure_door(self.door, hidden=True)
        self.call(CmdOpen(), "north", "You do not see one clear target by that name.")
        self.door.locks.add("interact:false()")
        configure_door(self.door, hidden=False)
        self.call(CmdOpen(), "north", "You do not see one clear target by that name.")

"""Command-level coverage for MOB-07's controlled-pet order grammar."""

from unittest.mock import patch

from commands.relationships import CmdOrder
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.mobile_relationships import acquire_pet
from typeclasses.characters import Character


class TestCmdOrder(EvenniaCommandTest):
    """Only a local controlled pet can receive one normal game command."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.hound = create_object(Character, key="Hound", location=self.room1)
        self.hound.db.is_player_character = False

    def test_order_needs_no_equals_operator_and_forwards_full_command_text(self):
        acquire_pet(self.char1, self.hound)

        with patch.object(self.hound, "execute_cmd") as execute_cmd:
            self.call(CmdOrder(), "Hound get all corpse", "Hound obeys.")

        execute_cmd.assert_called_once_with("get all corpse")

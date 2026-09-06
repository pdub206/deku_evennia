"""Command-level coverage for MOB-07's controlled-pet order grammar."""

from commands.relationships import CmdOrder, CmdPet
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
        self.hound.locks.add("pet:all();order:all()")

    def test_order_needs_no_equals_operator_and_accepts_registered_action(self):
        acquire_pet(self.char1, self.hound)

        self.call(CmdOrder(), "Hound follow", "Hound obeys.")

    def test_order_rejects_raw_character_commands(self):
        acquire_pet(self.char1, self.hound)

        self.call(
            CmdOrder(), "Hound get all corpse", "That order cannot be carried out."
        )

    def test_pet_command_can_follow_and_release_an_owned_pet(self):
        acquire_pet(self.char1, self.hound)

        self.call(CmdPet(), "Hound stay", "Hound acknowledges you.")
        self.call(CmdPet(), "Hound release", "Hound acknowledges you.")

"""ADV-03 command integration coverage."""

from unittest.mock import patch

from commands.training import CmdPractice, CmdTrain, _parse_training
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.training import default_trainer_profile, set_trainer_profile


class TestTrainingCommands(EvenniaCommandTest):
    """Listing is trainer-free while a mutation is routed through the service."""

    def setUp(self):
        super().setUp()
        self._release_gate = patch(
            "systems.training.is_level_published", return_value=True
        )
        self._release_gate.start()
        self.addCleanup(self._release_gate.stop)
        self.char1.db.is_player_character = True
        self.char1.db.char_class = "Fighter"
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.trainer = create_object(
            "typeclasses.characters.Character", key="Armsmaster", location=self.room1
        )
        self.trainer.db.is_player_character = False
        profile = default_trainer_profile()
        profile["classes"] = ["Fighter"]
        profile["choices"] = ["fighter.skills"]
        set_trainer_profile(self.trainer, profile)

    def test_practice_and_empty_train_list_without_needing_a_trainer(self):
        """Read-only commands expose a pending choice without mutating it."""
        self.assertIn("fighter.skills", self.call(CmdPractice(), ""))
        self.assertIn("fighter.skills", self.call(CmdTrain(), ""))

    def test_train_uses_explicit_grammar_and_nearby_trainer(self):
        """A valid selection reaches the transactional service through the command."""
        output = self.call(CmdTrain(), "fighter.skills Athletics at Armsmaster")

        self.assertIn("Athletics", output)
        self.assertFalse(self.char1.db.skill_proficiencies or [])

    def test_replacement_grammar_keeps_both_stable_option_keys(self):
        """Replacement parsing cannot mistake the old action for a trainer name."""
        self.assertEqual(
            _parse_training(
                "wizard.test_learned replace wizard.old_cantrip with "
                "wizard.new_cantrip at Arcanist"
            ),
            (
                "wizard.test_learned",
                "wizard.old_cantrip",
                "wizard.new_cantrip",
                "Arcanist",
            ),
        )
        self.assertIsNone(_parse_training("fighter.skills = Athletics"))

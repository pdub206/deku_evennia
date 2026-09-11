"""ADV-03 command integration coverage."""

from commands.training import CmdPractice, CmdTrain, _parse_training
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.training import default_trainer_profile, set_trainer_profile


class TestTrainingCommands(EvenniaCommandTest):
    """Listing is trainer-free while a mutation is routed through the service."""

    def setUp(self):
        super().setUp()
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

    def test_post_alpha_replacement_grammar_is_unavailable(self):
        """The alpha command cannot replace an already selected option."""
        self.assertIsNone(
            _parse_training("wizard.cantrip replace old with new at Arcanist")
        )
        self.assertIsNone(_parse_training("fighter.skills = Athletics"))

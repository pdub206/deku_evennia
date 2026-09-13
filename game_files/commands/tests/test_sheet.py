"""Command integration tests for canonical character statistics."""

from copy import deepcopy

from commands.sheet import CmdSheet
from commands.skills import CmdSkills
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one


class TestStatCommands(EvenniaCommandTest):
    """Sheet commands render values supplied by ``Character.stats``."""

    def setUp(self):
        super().setUp()
        self.char1.db.char_class = "Fighter"
        self.char1.db.species = "Human"
        self.char1.db.size = "Medium"
        self.char1.db.level = 1
        self.char1.db.hp_base = 10
        self.char1.db.hp_current = 12
        self.char1.db.dexterity = 14
        self.char1.db.constitution = 14
        self.char1.db.strength = 14
        self.char1.db.wisdom = 10
        self.char1.db.speed = 30
        self.char1.db.skill_proficiencies = ["Athletics"]
        self.char1.db.is_player_character = True
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)
        self.char1.db.hp_current = 12

    def test_score_uses_derived_values_and_reaction_label(self):
        output = self.call(CmdSheet(), "")

        self.assertIn("12/12", output)
        self.assertIn("Armor Class:", output)
        self.assertIn("12", output)
        self.assertIn("Reaction:", output)
        self.assertIn("+2", output)
        self.assertNotIn("Initiative:", output)
        self.assertIn("Next Level:", output)
        self.assertIn("300 XP (300 remaining)", output)

    def test_skills_uses_canonical_skill_bonus(self):
        output = self.call(CmdSkills(), "")

        athletics_line = next(
            line for line in output.splitlines() if "Athletics" in line
        )
        self.assertIn("+4", athletics_line)

    def test_score_returns_safe_partial_output_for_drift_without_mutating(self):
        """A malformed progression cannot invent a threshold or repair itself."""
        malformed = deepcopy(self.char1.db.class_progression)
        malformed["fingerprint"] = "stale"
        self.char1.db.class_progression = malformed

        output = self.call(CmdSheet(), "")

        self.assertIn("Class: Fighter", output)
        self.assertIn("needs staff review", output)
        self.assertNotIn("Next Level:", output)
        self.assertEqual(self.char1.db.class_progression, malformed)

"""Command integration tests for canonical character statistics."""

from commands.sheet import CmdSheet
from commands.skills import CmdSkills
from evennia.utils.test_resources import EvenniaCommandTest


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

    def test_score_uses_derived_values_and_reaction_label(self):
        output = self.call(CmdSheet(), "")

        self.assertIn("12/12", output)
        self.assertIn("Armor Class:", output)
        self.assertIn("12", output)
        self.assertIn("Reaction:", output)
        self.assertIn("+2", output)
        self.assertNotIn("Initiative:", output)

    def test_skills_uses_canonical_skill_bonus(self):
        output = self.call(CmdSkills(), "")

        athletics_line = next(
            line for line in output.splitlines() if "Athletics" in line
        )
        self.assertIn("+4", athletics_line)

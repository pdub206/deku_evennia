"""ADV-05 player and Builder command coverage."""

from copy import deepcopy

from commands.advancement import CmdAdvancement, CmdLevels
from commands.default_cmdsets import CharacterCmdSet
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import award_xp, initialize_level_one
from systems.training import record_chargen_choice


class TestAdvancementCommands(EvenniaCommandTest):
    """Advancement commands are registered, bounded, and read-only."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Fighter", hp_base=10)

    def test_commands_are_registered_and_staff_inspection_is_locked(self):
        """Players receive levels while only Builders receive diagnostics."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        keys = {command.key for command in cmdset.commands}
        self.assertIn("levels", keys)
        self.assertIn("@advancement", keys)
        self.assertTrue(CmdAdvancement().access(self.char1, "cmd"))
        self.char2.permissions.clear()
        self.assertFalse(CmdAdvancement().access(self.char2, "cmd"))

    def test_levels_shows_pending_resolved_resources_and_no_future_levels(self):
        """The complete player view includes choices without internal future data."""
        record_chargen_choice(self.char1, "Fighter", ("Athletics", "Acrobatics"))
        award_xp(self.char1, 900, source_kind="test", source_id="three")
        before = deepcopy(
            {
                attribute.key: attribute.value
                for attribute in self.char1.attributes.all()
            }
        )
        output = self.call(CmdLevels(), "")

        self.assertIn("Level 1", output)
        self.assertIn("Level 2", output)
        self.assertIn("Level 3", output)
        self.assertNotIn("Level 4", output)
        self.assertIn("Pending training: Fighting Style", output)
        self.assertIn("Trained: Skills: Athletics, Acrobatics", output)
        self.assertIn("Second Wind", output)
        self.assertEqual(
            {
                attribute.key: attribute.value
                for attribute in self.char1.attributes.all()
            },
            before,
        )

    def test_staff_output_has_provenance_and_player_failure_is_redacted(self):
        """Builders see bounded identity while player views hide repair detail."""
        award_xp(self.char1, 1, source_kind="test", source_id="identity")
        output = self.call(CmdAdvancement(), self.char1.key)
        repeated = self.call(CmdAdvancement(), self.char1.key)
        self.assertIn("Ledger schema:", output)
        self.assertIn("Last award identity:", output)
        self.assertIn("Grant provenance:", output)
        self.assertEqual(repeated, output)
        self.assertLess(len(output), 4096)

        malformed = deepcopy(self.char1.db.class_progression)
        malformed["fingerprint"] = "stale"
        self.char1.db.class_progression = malformed
        player = self.call(CmdLevels(), "")
        staff = self.call(CmdAdvancement(), self.char1.key)
        self.assertEqual(player, "Advancement information needs staff review.")
        self.assertIn("invalid_class_progression", staff)

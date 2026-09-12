"""Command-level coverage for ADV-04's diagnostic boundary."""

from commands.checks import CmdCheck
from commands.default_cmdsets import CharacterCmdSet
from evennia.utils.test_resources import EvenniaCommandTest


class TestCheckCommand(EvenniaCommandTest):
    """The Builder diagnostic is registered, bounded, and consequence-free."""

    def test_command_is_registered_and_builder_locked(self):
        """Ordinary players cannot invoke the staff-only diagnostic."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        self.assertTrue(any(command.key == "@check" for command in cmdset.commands))
        self.assertTrue(CmdCheck().access(self.char1, "cmd"))
        self.char2.permissions.clear()
        self.assertFalse(CmdCheck().access(self.char2, "cmd"))

    def test_visible_local_check_is_structured_and_side_effect_free(self):
        """A diagnostic reports public math without changing target state."""
        before = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char2.attributes.all()
        )
        output = self.call(CmdCheck(), "Char2 = Strength/Athletics 10")
        after = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char2.attributes.all()
        )
        self.assertIn("Char2: Strength (Athletics)", output)
        self.assertIn("vs DC 10", output)
        self.assertEqual(after, before)

    def test_invalid_dc_and_remote_target_fail_without_private_details(self):
        """Bad bounds and inaccessible targets expose no internal diagnostics."""
        output = self.call(CmdCheck(), "Char2 = Strength 31")
        self.assertIn("DC from 5 to 30", output)
        self.assertNotIn("Traceback", output)

        self.char2.move_to(self.room2, quiet=True)
        output = self.call(CmdCheck(), "Char2 = Strength 10")
        self.assertNotIn("Strength", output)
        self.assertNotIn("#", output)

"""
Tests for common game-command overrides (commands/generic.py).

Run from the game/ directory:
    evennia test --settings settings.py commands.tests.test_generic
"""

from commands.generic import CmdInventory
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest


class TestInventory(EvenniaCommandTest):
    """Inventory lists item names only; descriptions are for `look <item>`."""

    def test_lists_names_without_descriptions(self):
        sword = create_object(
            "typeclasses.objects.Item", key="a shortsword", location=self.char1
        )
        sword.db.desc = "This shortsword is nearly a foot and a half long."
        out = self.call(CmdInventory(), "")
        self.assertIn("a shortsword", out)
        self.assertNotIn("foot and a half", out)

    def test_empty_inventory(self):
        self.call(CmdInventory(), "", "You are not carrying anything.")

    def test_blocked_while_sleeping(self):
        create_object("typeclasses.objects.Item", key="a rock", location=self.char1)
        self.char1.db.position = "sleeping"
        self.call(CmdInventory(), "", "You are asleep")

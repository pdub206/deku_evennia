"""Command-level batch admission tests for RULES-05."""

from commands.generic import CmdGet, CmdGive
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest


class TestEncumbranceCommands(EvenniaCommandTest):
    """Get and give reject an entire over-capacity selected batch."""

    def item(self, key, location):
        """Create one zero-weight item at a deliberate source location."""
        return create_object("typeclasses.objects.Item", key=key, location=location)

    def test_get_batch_does_not_partially_move_when_count_limit_would_overflow(self):
        self.char1.db.carry_item_limit = 1
        first = self.item("a stone", self.room1)
        second = self.item("a stone", self.room1)

        self.call(CmdGet(), "2 stone", "You cannot carry any more items.")

        self.assertEqual(first.location, self.room1)
        self.assertEqual(second.location, self.room1)

    def test_give_batch_does_not_partially_move_when_target_limit_would_overflow(self):
        self.char1.db.carry_item_limit = 10
        self.char2.db.carry_item_limit = 1
        first = self.item("a stone", self.char1)
        second = self.item("a stone", self.char1)

        self.call(CmdGive(), "2 stone = Char2", "You cannot carry any more items.")

        self.assertEqual(first.location, self.char1)
        self.assertEqual(second.location, self.char1)

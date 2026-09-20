"""ITEM-02 wallet, pile, transfer, and concise command regression tests."""

from commands.generic import CmdCoins, CmdDrop, CmdGet, CmdGive
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.corpses import create_corpse, corpse_record
from systems.currency import balance, credit, debit


class TestCurrencyCommands(EvenniaCommandTest):
    """Player currency syntax requires no separators or filler words."""

    def setUp(self):
        super().setUp()
        self.char2.move_to(self.room1, quiet=True)

    def test_concise_item_and_coin_give(self):
        gem = create_object(
            "typeclasses.objects.Item", key="a gem", location=self.char1
        )
        credit(self.char1, 7, "setup:give", source="test")

        self.call(CmdGive(), "gem Char2", "You give a gem to Char2.")
        self.assertIs(gem.location, self.char2)
        self.call(CmdGive(), "3 coins Char2", "You give 3 coins to Char2.")
        self.assertEqual(balance(self.char1), 4)
        self.assertEqual(balance(self.char2), 3)

    def test_drop_and_pickup_money_pile(self):
        credit(self.char1, 5, "setup:drop", source="test")
        self.call(CmdDrop(), "2 coins", "You drop 2 coins.")
        pile = next(obj for obj in self.room1.contents if obj.db.type == "money")
        self.assertEqual(pile.db.amount, 2)
        self.call(CmdGet(), "2 coins", "You pick up 2 coins.")
        self.assertEqual(balance(self.char1), 5)

    def test_get_coins_from_corpse_without_from(self):
        credit(self.char2, 4, "setup:corpse", source="test")
        self.char2.db.is_player_character = False
        corpse = create_corpse(self.char2, "currency-command-death")

        self.call(
            CmdGet(), f"coins {corpse.key}", f"You take 4 coins from {corpse.key}."
        )
        self.assertEqual(balance(self.char1), 4)
        self.assertEqual(corpse_record(corpse).currency, 0)

    def test_display_pluralization(self):
        credit(self.char1, 1, "setup:display", source="test")
        self.call(CmdCoins(), "", "You have 1 coin.")


class TestCurrencyService(EvenniaCommandTest):
    """The canonical service validates and idempotently audits mutations."""

    def test_repeated_identity_does_not_repeat_credit(self):
        first = credit(self.char1, 5, "same", source="test")
        repeated = credit(self.char1, 5, "same", source="test")
        self.assertTrue(first.success)
        self.assertTrue(repeated.repeated, repr(repeated))
        self.assertEqual(balance(self.char1), 5)

    def test_invalid_and_insufficient_amounts_fail_safely(self):
        with self.assertRaises(ValueError):
            credit(self.char1, True, "bool", source="test")
        result = debit(self.char1, 1, "empty", source="test")
        self.assertFalse(result.success)
        self.assertEqual(balance(self.char1), 0)

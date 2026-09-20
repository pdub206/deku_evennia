"""ITEM-03B grammar, conservation, failure, replay, and shop editor regressions."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import CmdBuild, CmdBuildDone
from commands.default_cmdsets import CharacterCmdSet
from commands.shop_building import (
    CmdShopDel,
    CmdShopFields,
    CmdShopSet,
    CmdShopShow,
    CmdShopTransactions,
)
from commands.shops import CmdBuy, CmdList, CmdSell, CmdValue, resolve_shop
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import delete_prototype, save_prototype
from evennia.utils.test_resources import EvenniaCommandTest
from systems.currency import balance, maximum_balance
from systems.shops import (
    SHOP_STOCK_PROVENANCE_ATTRIBUTE,
    ShopError,
    process_shop_schedule,
    set_shop_profile,
    shop_price,
    shop_profile,
    trade_shop,
    visible_stock,
)
from typeclasses.characters import Character
from typeclasses.objects import Item


class TestShopTransactions(EvenniaCommandTest):
    """Exercise real ORM wallets/items and command parsing in a clean test world."""

    item_key = "item03b_test_blade"

    def tearDown(self) -> None:
        delete_prototype(self.item_key)
        super().tearDown()

    def setUp(self) -> None:
        super().setUp()
        save_prototype(
            {
                "prototype_key": self.item_key,
                "key": "test blade",
                "typeclass": "typeclasses.objects.Item",
                "type": "weapon",
                "weight": 1,
            }
        )
        self.npc = create_object(Character, key="Shopkeeper", location=self.room1)
        self.npc.db.is_player_character = False
        self.profile = {
            "version": 1,
            "profile_key": "test_shop",
            "access_lock": "shop:all()",
            "accepted_kinds": ["weapon"],
            "buy_markup": 125,
            "sell_markdown": 50,
            "open_hour": 8,
            "close_hour": 18,
            "wallet_opening_balance": 100,
            "stock": [{"prototype_key": self.item_key, "target_quantity": 2}],
        }
        set_shop_profile(self.npc, self.profile)
        self.npc.aliases.add("merchant")
        self.char1.db.currency = 200
        self.char2.location = self.room1
        self.char2.db.currency = 200
        self.item = create_object(Item, key="test blade", location=self.npc)
        self.item.db.type = "weapon"
        self.item.db.value = 3
        self.item.db.weight = 1

    def buy(self, identity: str = "buy-test", actor=None):
        """Run the exact-item service with a stable identity supplied by its caller."""
        return trade_shop(
            actor or self.char1,
            self.npc,
            self.item,
            buying=True,
            transaction_id=identity,
        )

    def assert_unchanged(
        self, location=None, actor_balance: int = 200, shop_balance: int = 100
    ) -> None:
        """Compare both cached runtime state and persisted item ownership."""
        expected = location or self.npc
        self.assertIs(self.item.location, expected)
        self.assertEqual(
            ObjectDB.objects.get(id=self.item.id).db_location_id, expected.id
        )
        self.assertEqual(
            (balance(self.char1), balance(self.npc)), (actor_balance, shop_balance)
        )
        self.assertIn(self.item, expected.contents)
        other = self.char1 if expected is self.npc else self.npc
        self.assertNotIn(self.item, other.contents)

    def test_command_registration_and_list_grammars(self) -> None:
        cmdset = CharacterCmdSet()
        for name in ("list", "value", "buy", "sell"):
            self.assertIsNotNone(cmdset.get(name))
        self.call(
            CmdList(), "", "Shopkeeper's stock:\ntest blade — 1 available — 4 coins"
        )
        self.call(
            CmdList(),
            "merchant",
            "Shopkeeper's stock:\ntest blade — 1 available — 4 coins",
        )

    def test_value_implicit_explicit_and_no_reservation(self) -> None:
        self.item.location = self.char1
        for args in ("blade", "blade at merchant"):
            self.call(CmdValue(), args, "Shopkeeper offers 1 coins for test blade.")
        self.assert_unchanged(self.char1)
        self.item.db.value = 20
        result = trade_shop(
            self.char1,
            self.npc,
            self.item,
            buying=False,
            transaction_id="changed-quote",
        )
        self.assertTrue(result.success, result.reason)
        self.assertEqual(result.price, 10)

    def test_buy_and_sell_grammars(self) -> None:
        for args in ("blade", "blade from merchant", "blade at merchant"):
            with self.subTest(args=args):
                self.item.location = self.npc
                with self.captureOnCommitCallbacks(execute=True):
                    self.call(CmdBuy(), args, None)
                self.assertIs(self.item.location, self.char1)
        for args in ("blade", "blade to merchant", "blade at merchant"):
            with self.subTest(args=args):
                self.item.location = self.char1
                with self.captureOnCommitCallbacks(execute=True):
                    self.call(CmdSell(), args, None)
                self.assertIs(self.item.location, self.npc)

    def test_multiple_shop_keywords_exact_ambiguous_and_closed(self) -> None:
        other = create_object(Character, key="Other Shopkeeper", location=self.room1)
        other.db.is_player_character = False
        other.aliases.add("other")
        set_shop_profile(other, {**self.profile, "profile_key": "other_shop"})
        self.call(CmdList(), "", "Which shopkeeper? Use their name or keywords.")
        self.assertIs(resolve_shop(self.char1, "merchant"), self.npc)
        self.assertIs(resolve_shop(self.char1, "other"), other)
        for cls, args in ((CmdBuy, "blade"), (CmdSell, "blade"), (CmdValue, "blade")):
            self.call(cls(), args, "Which shopkeeper? Use their name or keywords.")
        set_shop_profile(
            other,
            {
                **self.profile,
                "profile_key": "other_shop",
                "open_hour": 15,
                "close_hour": 18,
            },
        )
        self.assertIs(resolve_shop(self.char1), self.npc)
        other.location = self.room2
        with self.assertRaises(ShopError):
            resolve_shop(self.char1, "other")

    def test_rounding_minimum_and_invalid_value(self) -> None:
        for value, expected in ((1, (2, 1)), (3, (4, 1)), (10, (13, 5))):
            self.item.db.value = value
            self.assertEqual(
                (
                    shop_price(self.item, self.profile, buying=True),
                    shop_price(self.item, self.profile, buying=False),
                ),
                expected,
            )
        zero_percent = {**self.profile, "buy_markup": 0, "sell_markdown": 0}
        self.assertEqual(shop_price(self.item, zero_percent, buying=True), 1)
        for value in (0, -1, True, "3", 1.5):
            self.item.db.value = value
            with self.assertRaises(ShopError):
                shop_price(self.item, self.profile, buying=True)

    def test_hidden_unknown_and_depleted_stock(self) -> None:
        self.item.locks.add("view:false()")
        self.call(CmdList(), "", "That shop has no stock available.")
        self.call(CmdBuy(), "blade", "That item is unavailable.")
        self.assertFalse(self.buy().success)
        self.item.locks.add("view:all()")
        self.call(CmdBuy(), "missing", "That item is unavailable.")
        self.assertTrue(self.buy("visible").success)
        self.call(CmdBuy(), "blade", "That item is unavailable.")

    def test_wallet_funds_and_limits_both_directions(self) -> None:
        cases = (
            (True, 0, 100),
            (True, 200, maximum_balance()),
            (False, 200, 0),
            (False, maximum_balance(), 100),
        )
        for index, (buying, actor_coins, shop_coins) in enumerate(cases):
            with self.subTest(buying=buying, actor=actor_coins, shop=shop_coins):
                self.char1.db.currency = actor_coins
                self.npc.db.currency = shop_coins
                self.item.location = self.npc if buying else self.char1
                result = trade_shop(
                    self.char1,
                    self.npc,
                    self.item,
                    buying=buying,
                    transaction_id=f"wallet-{index}",
                )
                self.assertFalse(result.success)
                self.assert_unchanged(
                    self.npc if buying else self.char1, actor_coins, shop_coins
                )

    def test_actor_and_shop_capacity(self) -> None:
        for buying in (True, False):
            self.item.location = self.npc if buying else self.char1
            self.item.db.weight = 99999
            result = trade_shop(
                self.char1,
                self.npc,
                self.item,
                buying=buying,
                transaction_id=f"capacity-{buying}",
            )
            self.assertFalse(result.success)
            self.assert_unchanged(self.npc if buying else self.char1)

    def test_every_excluded_sale_category(self) -> None:
        self.item.location = self.char1
        for attr, value in (
            ("type", "money"),
            ("type", "corpse"),
            ("type", "armor"),
            ("value", 0),
            ("worn_location", "right_hand"),
            ("no_drop", True),
            ("account_bound", True),
        ):
            old = self.item.attributes.get(attr)
            self.item.attributes.add(attr, value)
            self.call(
                CmdValue(),
                "blade",
                (
                    "That item cannot be traded."
                    if attr != "value"
                    else "Shops only trade items with a positive whole-coin value."
                ),
            )
            result = trade_shop(
                self.char1,
                self.npc,
                self.item,
                buying=False,
                transaction_id=f"excluded-{attr}-{value}",
            )
            self.assertFalse(result.success)
            self.assert_unchanged(self.char1)
            self.item.attributes.add(attr, old)
        self.item.db.type = "container"
        set_shop_profile(
            self.npc, {**self.profile, "stock": [], "accepted_kinds": ["container"]}
        )
        child = create_object(Item, key="inside", location=self.item)
        result = trade_shop(
            self.char1, self.npc, self.item, buying=False, transaction_id="nonempty"
        )
        self.assertFalse(result.success)
        child.delete()
        self.item.db.type = "weapon"
        set_shop_profile(self.npc, self.profile)
        self.item.locks.add("drop:false()")
        self.assertFalse(
            trade_shop(
                self.char1,
                self.npc,
                self.item,
                buying=False,
                transaction_id="nodrop-lock",
            ).success
        )

    def test_duplicate_two_buyers_reload_and_receipts_survive_ledger_pruning(
        self,
    ) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            first = self.buy("durable")
        self.assertTrue(first.success, first.reason)
        self.assert_unchanged(self.char1, 196, 104)
        self.char1.attributes.remove("currency_ledger")
        self.char1.attributes.reset_cache()
        self.char1.refresh_from_db()
        repeated = self.buy("durable")
        self.assertTrue(repeated.success)
        self.assertTrue(repeated.repeated)
        self.assertFalse(self.buy("second-buyer", self.char2).success)
        self.assertEqual(balance(self.char2), 200)
        self.assert_unchanged(self.char1, 196, 104)
        other = create_object(Item, key="other blade", location=self.npc)
        other.db.type = "weapon"
        other.db.value = 10
        mismatch = trade_shop(
            self.char1, self.npc, other, buying=True, transaction_id="durable"
        )
        self.assertFalse(mismatch.success)
        self.assertTrue(mismatch.repeated)

    def test_failure_receipt_is_stable(self) -> None:
        self.char1.db.currency = 0
        first = self.buy("failed")
        self.assertFalse(first.success)
        self.char1.db.currency = 200
        retry = self.buy("failed")
        self.assertFalse(retry.success)
        self.assertTrue(retry.repeated)
        self.assert_unchanged()

    def test_reentrant_buyer_cannot_reserve_same_item(self) -> None:
        outcomes = []

        def nested(*args, **kwargs):
            outcomes.append(self.buy("nested", self.char2))
            return True

        with patch.object(self.item, "at_pre_give", side_effect=nested):
            self.assertTrue(self.buy("outer").success)
        self.assertFalse(outcomes[0].success)
        self.assertEqual(balance(self.char2), 200)

    def test_precommit_changes_reject_and_roll_back(self) -> None:
        def change_value(*args):
            self.item.db.value = 100
            return True

        def change_profile(*args):
            set_shop_profile(self.npc, {**self.profile, "buy_markup": 200})
            return True

        def change_lock(*args):
            self.npc.locks.add("shop:false()")
            return True

        def change_owner(*args):
            self.item.location = self.room2
            return True

        for index, mutate in enumerate(
            (change_value, change_profile, change_lock, change_owner)
        ):
            with patch.object(self.item, "at_pre_give", side_effect=mutate):
                result = self.buy(f"change-{index}")
            self.assertFalse(result.success, result)
            self.assert_unchanged()
            self.assertEqual(self.item.db.value, 3)
        with patch("systems.shops.current_shop_hour", side_effect=(12, 18)):
            self.assertFalse(self.buy("hours").success)
        self.assert_unchanged()

    def test_rollback_transfer_and_post_hook_phases(self) -> None:
        for hook in (
            "at_pre_give",
            "at_pre_get",
            "at_pre_move",
            "at_post_move",
            "at_give",
        ):
            with self.subTest(hook=hook), patch.object(
                self.item, hook, side_effect=RuntimeError("internal path")
            ):
                result = self.buy(f"hook-{hook}")
            self.assertFalse(result.success)
            self.assertNotIn("internal path", result.reason)
            self.assert_unchanged()
        with patch.object(self.item, "move_to", return_value=False):
            self.assertFalse(self.buy("move-false").success)
        self.assert_unchanged()
        from systems.currency import transfer

        def failed_payment(*args, **kwargs):
            transfer(*args, **kwargs)
            raise RuntimeError("after debit")

        with patch("systems.currency.transfer", side_effect=failed_payment):
            self.assertFalse(self.buy("payment-exception").success)
        self.assert_unchanged()

    def test_messages_only_after_commit_once_and_delivery_failure(self) -> None:
        with patch.object(self.char1, "msg") as private, patch.object(
            self.room1, "msg_contents"
        ) as public:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(self.buy("messages").success)
                private.assert_not_called()
                public.assert_not_called()
            private.assert_called_once()
            public.assert_called_once()
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(self.buy("messages").repeated)
            private.assert_called_once()
            public.assert_called_once()
        self.item.location = self.npc
        with patch.object(
            self.char1, "msg", side_effect=RuntimeError("network")
        ), patch.object(self.room1, "msg_contents") as public:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(self.buy("message-failure").success)
            public.assert_called_once()
            self.assertTrue(self.buy("message-failure").repeated)
            public.assert_called_once()
        self.assert_unchanged(self.char1, 192, 108)

    def test_actual_stock_and_provenance(self) -> None:
        process_shop_schedule(self.npc, self.profile, day=1, hour=12)
        authored = next(obj for obj in self.npc.contents if obj is not self.item)
        authored.db.value = 3
        self.assertTrue(authored.attributes.has(SHOP_STOCK_PROVENANCE_ATTRIBUTE))
        bought = trade_shop(
            self.char1, self.npc, authored, buying=True, transaction_id="authored-buy"
        )
        self.assertTrue(bought.success, bought.reason)
        self.assertFalse(authored.attributes.has(SHOP_STOCK_PROVENANCE_ATTRIBUTE))
        sold = trade_shop(
            self.char1, self.npc, authored, buying=False, transaction_id="authored-sell"
        )
        self.assertTrue(sold.success, sold.reason)
        self.assertIn(authored, visible_stock(self.npc, self.char1))
        next_day = process_shop_schedule(self.npc, self.profile, day=2, hour=12)
        self.assertEqual(next_day.created, 1)

    def test_invalid_and_bulk_grammars(self) -> None:
        for cls, args in (
            (CmdBuy, ""),
            (CmdBuy, "all"),
            (CmdSell, "all"),
            (CmdValue, ""),
            (CmdBuy, "blade from "),
            (CmdSell, "x" * 201),
        ):
            self.call(cls(), args, f"Usage: {cls.key} <item>")
            self.assert_unchanged()

    def test_shop_editor_attach_fields_validation_wallet_and_detach(self) -> None:
        npc = create_object(Character, key="New Merchant", location=self.room1)
        npc.db.is_player_character = False
        self.call(CmdBuild(), f"new shop #{npc.id}", None)
        self.assertIs(self.char1.ndb._build_target, npc)
        self.assertEqual(shop_profile(npc)["profile_key"], f"shop_{npc.id}")
        self.call(CmdShopFields(), "", None)
        self.call(CmdShopSet(), "buy_markup 150", "Set buy_markup to: 150")
        self.call(
            CmdShopSet(),
            "sell_markdown 151",
            "Invalid shop field: A shop's sell price cannot exceed its buy price.",
        )
        self.assertEqual(shop_profile(npc)["sell_markdown"], 50)
        self.call(
            CmdShopSet(),
            "wallet_opening_balance 1000",
            "Set wallet_opening_balance to: 1000",
        )
        self.assertEqual(balance(npc), 0)
        self.call(
            CmdShopSet(),
            'accepted_kinds ["weapon"]',
            "Set accepted_kinds to: ['weapon']",
        )
        self.call(
            CmdShopSet(),
            f'stock [{{"prototype_key": "{self.item_key}", "target_quantity": 1}}]',
            None,
        )
        self.call(CmdShopShow(), "", None)
        self.call(CmdShopTransactions(), "", "No shop transactions recorded.")
        self.call(CmdBuildDone(), "", None)
        self.assertIsNone(self.char1.ndb._build_target)
        self.call(CmdBuild(), f"shop #{npc.id}", None)
        self.call(CmdShopDel(), "", "Detach this shop? Type del again to confirm.")
        self.call(
            CmdShopDel(),
            "",
            "{'prompt': ''}|Shop detached. NPC, inventory, and wallet preserved.",
        )
        self.assertTrue(ObjectDB.objects.filter(id=npc.id).exists())
        with self.assertRaises(ShopError):
            shop_profile(npc)

    def test_shop_editor_requires_npc_target_and_builder_locks(self) -> None:
        self.call(
            CmdBuild(),
            "new shop",
            "Usage: edit new shop <npc> / edit shop <npc>",
        )
        self.call(
            CmdBuild(),
            f"new shop #{self.char1.id}",
            "Shops must attach to an NPC.",
        )
        self.call(
            CmdBuild(),
            f"new shop #{self.npc.id}",
            "That NPC already has a shop. Use edit shop <npc>.",
        )
        for cls in (
            CmdShopSet,
            CmdShopFields,
            CmdShopShow,
            CmdShopTransactions,
            CmdShopDel,
        ):
            self.assertIn("perm(Builder)", cls().locks)

    def test_sale_hook_failures_restore_exact_item_and_wallets(self) -> None:
        self.item.location = self.char1
        for hook in (
            "at_pre_give",
            "at_pre_drop",
            "at_pre_move",
            "at_post_move",
            "at_give",
        ):
            with self.subTest(hook=hook), patch.object(
                self.item, hook, side_effect=RuntimeError("hook failure")
            ):
                result = trade_shop(
                    self.char1,
                    self.npc,
                    self.item,
                    buying=False,
                    transaction_id=f"sell-hook-{hook}",
                )
            self.assertFalse(result.success)
            self.assert_unchanged(self.char1)

    def test_post_hook_price_wallet_and_location_changes_roll_back(self) -> None:
        def change_price(*args):
            self.item.db.value = 10

        def change_wallet(*args):
            self.npc.db.currency = 500

        def change_location(*args):
            self.item.location = self.room2

        for index, mutate in enumerate((change_price, change_wallet, change_location)):
            with patch.object(self.item, "at_give", side_effect=mutate):
                self.assertFalse(self.buy(f"post-change-{index}").success)
            self.assert_unchanged()
            self.assertEqual(self.item.db.value, 3)

    def test_failed_display_and_deleted_item_hook_restore_state(self) -> None:
        with patch.object(
            self.item, "get_display_name", side_effect=RuntimeError("display")
        ):
            self.assertFalse(self.buy("display-failure").success)
        self.assert_unchanged()
        item_id = self.item.id
        with patch.object(
            self.item, "at_give", side_effect=lambda *args: self.item.delete()
        ):
            self.assertFalse(self.buy("deleted-hook").success)
        self.assertEqual(self.item.id, item_id)
        self.assert_unchanged()

    def test_diagnostics_record_failures_and_receipts_do_not_duplicate_history(
        self,
    ) -> None:
        self.char1.db.currency = 0
        result = self.buy("audit-failure")
        self.assertFalse(result.success)
        self.assertEqual(len(self.npc.db.shop_transaction_history), 1)
        self.assertFalse(self.npc.db.shop_transaction_history[0]["success"])
        self.assertTrue(self.buy("audit-failure").repeated)
        self.assertEqual(len(self.npc.db.shop_transaction_history), 1)

    def test_identical_stock_quantity_and_explicit_grammar_with_multiple_shops(
        self,
    ) -> None:
        copy = create_object(Item, key="test blade", location=self.npc)
        copy.db.type = "weapon"
        copy.db.value = 3
        self.call(
            CmdList(),
            "merchant",
            "Shopkeeper's stock:\ntest blade — 2 available — 4 coins",
        )
        other = create_object(Character, key="Other Merchant", location=self.room1)
        other.db.is_player_character = False
        set_shop_profile(other, {**self.profile, "profile_key": "second"})
        self.call(CmdBuy(), "blade from merchant", None)
        self.assertIs(self.item.location, self.char1)
        self.call(
            CmdValue(), "blade at merchant", "Shopkeeper offers 1 coins for test blade."
        )
        self.call(CmdSell(), "blade to merchant", None)
        self.assertIs(self.item.location, self.npc)

    def test_unseen_shop_and_dark_room_reject_trades(self) -> None:
        self.npc.locks.add("view:false()")
        self.call(CmdList(), "", "No matching open shop is available here.")
        self.assertFalse(self.buy("unseen-shop").success)
        self.npc.locks.add("view:all()")
        from systems.room_environment import set_room_environment_value

        set_room_environment_value(self.room1, "light", "dark")
        self.call(CmdList(), "", "You cannot see a shop here.")
        self.assertFalse(self.buy("dark-shop").success)
        self.assert_unchanged()

    def test_shop_editor_creates_by_name_and_edits_by_keyword(self) -> None:
        npc = create_object(Character, key="Named Merchant", location=self.room1)
        npc.db.is_player_character = False
        npc.aliases.add("vendor")
        self.call(
            CmdBuild(), "new shop Named Merchant", "Editing shop on Named Merchant"
        )
        self.assertIs(self.char1.ndb._build_target, npc)
        self.assertEqual(shop_profile(npc)["profile_key"], f"shop_{npc.id}")
        self.call(CmdBuildDone(), "", None)
        self.call(CmdBuild(), "shop vendor", "Editing shop on Named Merchant")
        self.assertIs(self.char1.ndb._build_target, npc)

    def test_shop_editor_unknown_and_ambiguous_names_do_not_attach(self) -> None:
        self.call(CmdBuild(), "new shop Missing Merchant", None)
        self.assertIsNone(self.char1.ndb._build_target)
        npcs = [
            create_object(Character, key="Duplicate Merchant", location=self.room1)
            for _ in range(2)
        ]
        for npc in npcs:
            npc.db.is_player_character = False
        self.call(CmdBuild(), "new shop Duplicate Merchant", None)
        self.assertIsNone(self.char1.ndb._build_target)
        for npc in npcs:
            with self.assertRaises(ShopError):
                shop_profile(npc)
        self.call(CmdBuild(), "shop Duplicate Merchant", None)
        self.assertIsNone(self.char1.ndb._build_target)
        self.call(
            CmdBuild(), f"new shop #{npcs[0].id}", "Editing shop on Duplicate Merchant"
        )
        self.assertIs(self.char1.ndb._build_target, npcs[0])

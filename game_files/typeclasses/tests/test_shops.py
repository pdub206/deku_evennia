"""ITEM-03A shop schema, stock, schedule, and MOB-06 integration tests."""

from evennia import create_object
from evennia.prototypes.prototypes import delete_prototype, save_prototype
from evennia.utils.test_resources import EvenniaTest
from systems.mobile_specials import (SpecialEvent, dispatch_specials,
                                     mobile_specials, set_mobile_specials)
from systems.shops import (ShopError, process_shop_schedule, shop_availability,
                           shop_is_open, shop_snapshot, validate_shop_profile)
from typeclasses.characters import Character
from typeclasses.objects import Item


class TestShops(EvenniaTest):
    """Shop tests create disposable NPCs; no persistent content is required."""

    item_key = "item03a_test_blade"

    def setUp(self):
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

    def tearDown(self):
        delete_prototype(self.item_key)
        super().tearDown()

    def test_schema_rejects_bad_fields_bounds_kinds_stock_and_code(self):
        self.assertEqual(validate_shop_profile(self.profile), self.profile)
        bad_values = [
            ("profile_key", "bad key"),
            ("access_lock", "view:all()"),
            ("accepted_kinds", ["unknown"]),
            ("buy_markup", -1),
            ("sell_markdown", 126),
            ("open_hour", 24),
            ("close_hour", 8),
            ("wallet_opening_balance", -1),
            ("stock", [{"prototype_key": "missing", "target_quantity": 1}]),
            ("stock", [{"prototype_key": self.item_key, "target_quantity": object()}]),
        ]
        for field, value in bad_values:
            with self.subTest(field=field, value=value), self.assertRaises(ShopError):
                validate_shop_profile({**self.profile, field: value})
        duplicate = [self.profile["stock"][0], self.profile["stock"][0]]
        with self.assertRaises(ShopError):
            validate_shop_profile({**self.profile, "stock": duplicate})

    def test_live_copy_isolated_wallet_lock_and_non_npc_rejection(self):
        set_mobile_specials(
            self.npc,
            {
                "version": 1,
                "behaviors": [{"key": "shopkeeper", "config": self.profile}],
            },
        )
        self.profile["stock"][0]["target_quantity"] = 9
        self.assertEqual(self.npc.db.currency, 100)
        self.assertEqual(
            shop_snapshot(
                self.npc, self.npc.db.mobile_specials["behaviors"][0]["config"]
            )["definition"]["stock"][0]["target_quantity"],
            2,
        )
        with self.assertRaises(ShopError):
            set_mobile_specials(
                self.char1,
                {
                    "version": 1,
                    "behaviors": [
                        {"key": "shopkeeper", "config": {**self.profile, "stock": []}}
                    ],
                },
            )

    def test_schedule_tops_up_once_preserves_sold_items_and_skips_days(self):
        first = process_shop_schedule(self.npc, self.profile, day=4, hour=8)
        self.assertEqual((first.status, first.created), ("restocked", 2))
        authored = list(self.npc.contents)
        authored[0].delete()
        sold = create_object(Item, key="sold blade", location=self.npc)
        sold.tags.add(self.item_key, category="from_prototype")
        repeated = process_shop_schedule(self.npc, self.profile, day=4, hour=9)
        older = process_shop_schedule(self.npc, self.profile, day=3, hour=9)
        self.assertEqual((repeated.created, older.created), (0, 0))
        next_day = process_shop_schedule(self.npc, self.profile, day=6, hour=9)
        self.assertEqual(next_day.created, 1)
        self.assertIn(sold, self.npc.contents)
        snapshot = shop_snapshot(self.npc, self.profile)["live_stock"][0]
        self.assertEqual(
            (snapshot["actual_quantity"], snapshot["authored_quantity"]), (3, 2)
        )

    def test_hours_availability_and_mob06_adapter_fail_closed(self):
        self.assertTrue(shop_is_open(self.profile, 8))
        self.assertFalse(shop_is_open(self.profile, 18))
        overnight = {**self.profile, "open_hour": 20, "close_hour": 4}
        self.assertTrue(shop_is_open(overnight, 23))
        self.assertTrue(shop_is_open(overnight, 2))
        self.assertFalse(shop_is_open(overnight, 12))
        set_mobile_specials(
            self.npc,
            {
                "version": 1,
                "behaviors": [{"key": "shopkeeper", "config": self.profile}],
            },
        )
        self.assertEqual(len(mobile_specials(self.npc)["behaviors"]), 1)
        self.assertTrue(
            shop_availability(self.npc, self.char1, self.profile, 9).available
        )
        self.npc.db.hp_current = 0
        self.assertEqual(
            shop_availability(self.npc, self.char1, self.profile, 9).reason, "dead"
        )
        self.npc.db.hp_current = 1
        result = dispatch_specials(
            self.npc,
            SpecialEvent("service", service="schedule", data={"day": 7, "hour": 9}),
        )
        self.assertTrue(result.acted, result.outcomes)

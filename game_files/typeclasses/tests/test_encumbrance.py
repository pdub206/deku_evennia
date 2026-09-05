"""Tests for the shared RULES-05 load calculator and movement admission."""

from decimal import Decimal

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.encumbrance import can_receive, character_load, pounds_to_units
from world.build_schema import as_weight


class TestEncumbrance(EvenniaTest):
    """PCs and NPCs use the same recursive load and capacity policy."""

    def item(self, key, location=None, weight=0.0, **attributes):
        """Create a data-driven item with explicit normalized test inputs."""
        values = [("weight", weight), *attributes.items()]
        return create_object(
            "typeclasses.objects.Item",
            key=key,
            location=location or self.room1,
            attributes=values,
        )

    def setUp(self):
        super().setUp()
        self.char1.stats.set_ability_score("Strength", 1)
        self.char1.db.size = "Medium"
        self.char1.db.carry_item_limit = 10

    def test_exact_and_one_over_weight_limits(self):
        exact = self.item("exact", weight=15)
        over = self.item("over", weight=0.01)

        self.assertTrue(exact.move_to(self.char1, quiet=True))
        self.assertFalse(over.move_to(self.char1, quiet=True))
        load = character_load(self.char1)

        self.assertEqual((load.count, load.weight, load.weight_limit), (1, 15.0, 15.0))
        self.assertEqual(over.location, self.room1)

    def test_exact_and_one_over_count_limits(self):
        self.char1.db.carry_item_limit = 2
        first = self.item("first")
        second = self.item("second")
        third = self.item("third")

        self.assertTrue(first.move_to(self.char1, quiet=True))
        self.assertTrue(second.move_to(self.char1, quiet=True))
        self.assertFalse(third.move_to(self.char1, quiet=True))
        self.assertEqual(character_load(self.char1).count, 2)

    def test_nested_container_counts_every_object_and_weight_once(self):
        bag = self.item("bag", weight=1, type="container", capacity=20)
        pouch = self.item("pouch", weight=1, type="container", capacity=10)
        coin = self.item("coin", weight=0.5)

        self.assertTrue(bag.move_to(self.char1, quiet=True))
        self.assertTrue(pouch.move_to(bag, quiet=True))
        self.assertTrue(coin.move_to(pouch, quiet=True))

        self.assertEqual(character_load(self.char1).count, 3)
        self.assertEqual(character_load(self.char1).weight, 2.5)

    def test_destination_container_includes_descendant_weight_and_fails_closed(self):
        bag = self.item("bag", weight=0, type="container", capacity=1)
        stone = self.item("stone", weight=1)
        extra = self.item("extra", weight=0.01)
        broken = self.item("broken", weight=0, type="container")
        pebble = self.item("pebble", weight=0)

        self.assertTrue(bag.move_to(self.char1, quiet=True))
        self.assertTrue(stone.move_to(bag, quiet=True))
        self.assertFalse(extra.move_to(bag, quiet=True))
        self.assertTrue(broken.move_to(self.char1, quiet=True))
        self.assertFalse(pebble.move_to(broken, quiet=True))

    def test_same_carrier_rearrangement_does_not_consume_more_character_load(self):
        left = self.item("left", type="container", capacity=2)
        right = self.item("right", type="container", capacity=2)
        stone = self.item("stone", weight=1)
        self.assertTrue(left.move_to(self.char1, quiet=True))
        self.assertTrue(right.move_to(self.char1, quiet=True))
        self.assertTrue(stone.move_to(left, quiet=True))
        before = character_load(self.char1)

        self.assertTrue(stone.move_to(right, quiet=True))
        self.assertEqual(character_load(self.char1), before)

    def test_batch_preflight_is_all_or_nothing(self):
        self.char1.db.carry_item_limit = 1
        first = self.item("first")
        second = self.item("second")

        result = can_receive(self.char1, [first, second])

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "character_count_limit")
        self.assertEqual(first.location, self.room1)
        self.assertEqual(second.location, self.room1)

    def test_invalid_weights_are_rejected_and_legacy_missing_weight_is_zero(self):
        legacy = create_object(
            "typeclasses.objects.Item", key="legacy", location=self.room1
        )
        legacy.attributes.remove("weight")
        malformed = self.item("malformed", weight="lots")

        self.assertEqual(pounds_to_units(Decimal("1.235")), 124)
        self.assertEqual(as_weight("1.235"), 1.24)
        self.assertTrue(legacy.move_to(self.char1, quiet=True))
        self.assertFalse(malformed.move_to(self.char1, quiet=True))
        for raw in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ValueError):
                as_weight(raw)

    def test_dynamic_capacity_changes_preserve_items_but_block_traversal(self):
        stone = self.item("stone", weight=20)
        self.assertTrue(
            stone.move_to(
                self.char1,
                quiet=True,
                encumbrance_bypass="test existing overloaded load",
            )
        )
        self.assertTrue(character_load(self.char1).overloaded)
        self.assertFalse(self.char1.at_pre_move(self.room2, move_type="traverse"))
        self.assertTrue(stone.move_to(self.room1, quiet=True, move_type="drop"))

    def test_npc_uses_the_same_admission_policy(self):
        self.char2.stats.set_ability_score("Strength", 1)
        heavy = self.item("heavy", weight=16)

        self.assertFalse(heavy.move_to(self.char2, quiet=True))
        self.assertEqual(heavy.location, self.room1)

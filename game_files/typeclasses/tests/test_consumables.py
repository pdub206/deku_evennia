"""ITEM-04A food and liquid transaction coverage."""

from __future__ import annotations

from commands.building import _apply_field
from commands.consumables import CmdDrink, CmdEat, CmdPour, CmdTaste
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.consumables import (
    ConsumableError,
    drink,
    eat,
    food_state,
    pour,
    set_food_profile,
    set_liquid_profile,
    taste,
)
from systems.item_resources import resource_state, set_resource_profile
from typeclasses.objects import Item
from world.build_schema import TYPE_FIELDS, as_food_profile, as_liquid_profile


def liquid(current: int, maximum: int = 3, key: str = "water") -> dict:
    """Build the primitive ITEM-05B liquid profile used by ITEM-04A."""
    return {
        "version": 1,
        "kind": "liquid",
        "resource_key": key,
        "current": current,
        "maximum": maximum,
        "recharge": "refill",
        "recharge_amount": 0,
    }


class TestConsumables(EvenniaTest):
    """Food and liquid state shares the standard item mutation lane."""

    def food(self, portions: int = 2):
        item = create_object(Item, key="rations", location=self.char1)
        item.db.type = "food"
        set_food_profile(item, {"version": 1, "portions": portions, "effect": None})
        return item

    def vessel(
        self,
        name: str,
        current: int,
        *,
        location=None,
        item_type="drinkcon",
        key="water",
    ):
        item = create_object(Item, key=name, location=location or self.char1)
        item.db.type = item_type
        set_resource_profile(item, liquid(current, key=key))
        set_liquid_profile(
            item,
            {
                "version": 1,
                "liquid_key": key,
                "inexhaustible": item_type == "fountain",
            },
        )
        return item

    def test_food_spends_once_and_deletes_on_last_portion(self):
        item = self.food(2)
        self.assertTrue(eat(self.char1, item, identity="meal.one"))
        self.assertEqual(food_state(item)["remaining"], 1)
        self.assertFalse(eat(self.char1, item, identity="meal.one"))
        self.assertTrue(eat(self.char1, item, identity="meal.two"))
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_food_and_drink_require_direct_ownership(self):
        item = self.food()
        item.move_to(self.room1, quiet=True)
        with self.assertRaises(ConsumableError):
            eat(self.char1, item)
        bottle = self.vessel("bottle", 1, location=self.char2)
        with self.assertRaises(ConsumableError):
            drink(self.char1, bottle)

    def test_drink_taste_and_fountain_rules(self):
        bottle = self.vessel("bottle", 2)
        self.assertEqual(taste(self.char1, bottle), "It tastes clean and plain.")
        self.assertTrue(drink(self.char1, bottle, identity="drink.one"))
        self.assertEqual(resource_state(bottle)["current"], 1)


class TestConsumableCommands(EvenniaCommandTest):
    """The player command surface reaches the same validated transaction APIs."""

    def vessel(
        self,
        name: str,
        current: int,
        *,
        location=None,
        item_type="drinkcon",
        key="water",
    ):
        """Create a valid directly usable liquid source for command integration."""
        item = create_object(Item, key=name, location=location or self.char1)
        item.db.type = item_type
        set_resource_profile(item, liquid(current, key=key))
        set_liquid_profile(
            item,
            {
                "version": 1,
                "liquid_key": key,
                "inexhaustible": item_type == "fountain",
            },
        )
        return item

    def test_eat_and_taste_commands(self):
        food = create_object(Item, key="rations", location=self.char1)
        food.db.type = "food"
        set_food_profile(food, {"version": 1, "portions": 2, "effect": None})
        bottle = create_object(Item, key="bottle", location=self.char1)
        bottle.db.type = "drinkcon"
        set_resource_profile(bottle, liquid(2))
        set_liquid_profile(
            bottle, {"version": 1, "liquid_key": "water", "inexhaustible": False}
        )
        self.call(CmdEat(), "rations", "You eat rations.")
        self.call(CmdTaste(), "bottle", "It tastes clean and plain.")
        self.call(CmdDrink(), "bottle", "You drink from bottle.")
        self.call(CmdPour(), "bottle into out", "You pour it out.")
        fountain = self.vessel("fountain", 1, location=self.room1, item_type="fountain")
        self.assertTrue(drink(self.char1, fountain, identity="fountain.one"))
        self.assertEqual(resource_state(fountain)["current"], 1)
        with self.assertRaises(ConsumableError):
            pour(self.char1, fountain, bottle)

    def test_pour_conserves_whole_servings_or_discards(self):
        source, target = self.vessel("jug", 3), self.vessel("bottle", 2)
        self.assertEqual(pour(self.char1, source, target), 1)
        self.assertEqual(
            (resource_state(source)["current"], resource_state(target)["current"]),
            (2, 3),
        )
        self.assertEqual(pour(self.char1, source, None), 2)
        self.assertEqual(resource_state(source)["current"], 0)

    def test_empty_container_adopts_the_poured_liquid(self):
        source = self.vessel("ale jug", 2, key="ale")
        target = self.vessel("bottle", 0)
        self.assertEqual(pour(self.char1, source, target), 2)
        self.assertEqual(taste(self.char1, target), "It tastes malty and bitter.")

    def test_fountain_cannot_be_carried_without_a_staff_bypass(self):
        fountain = self.vessel("fountain", 1, location=self.room1, item_type="fountain")
        self.assertFalse(fountain.move_to(self.char1, quiet=True))
        self.assertIs(fountain.location, self.room1)

    def test_combat_policy_denies_consumption_without_spend(self):
        bottle = self.vessel("bottle", 1)
        self.char1.db.position = "sleeping"
        with self.assertRaises(ConsumableError):
            drink(self.char1, bottle)
        self.assertEqual(resource_state(bottle)["current"], 1)

    def test_builder_profiles_validate_and_attach_to_matching_item_types(self):
        food = create_object(Item, key="rations", location=self.char1)
        food.db.type = "food"
        food_value = as_food_profile('{"version":1,"portions":3,"effect":null}')
        _apply_field(food, "food", TYPE_FIELDS["food"]["food"], food_value)
        self.assertEqual(food_state(food)["remaining"], 3)
        bottle = self.vessel("bottle", 1)
        liquid_value = as_liquid_profile(
            '{"version":1,"liquid_key":"water","inexhaustible":false}'
        )
        _apply_field(
            bottle,
            "liquid",
            TYPE_FIELDS["drinkcon"]["liquid"],
            liquid_value,
        )
        self.assertEqual(taste(self.char1, bottle), "It tastes clean and plain.")
        with self.assertRaises(ValueError):
            as_food_profile('{"version":1,"portions":0,"effect":null}')

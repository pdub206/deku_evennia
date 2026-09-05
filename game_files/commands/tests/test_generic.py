"""
Tests for common game-command overrides (commands/generic.py).

Run from the game/ directory:
    evennia test --settings settings.py commands.tests.test_generic
"""

from typing import Any

from commands.generic import CmdInventory, CmdJunk, CmdLook, CmdRemove, CmdWear
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import save_prototype, search_prototype
from evennia.prototypes.spawner import spawn
from evennia.utils.test_resources import EvenniaCommandTest
from systems.equipment import WEAR_LOCATIONS


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


class TestJunk(EvenniaCommandTest):
    """Junk destroys a carried item instance without touching its prototype."""

    PROTOTYPE_KEY = "test_junk_iron_sword"

    def test_deletes_carried_instance_but_preserves_prototype(self):
        save_prototype(
            {
                "prototype_key": self.PROTOTYPE_KEY,
                "key": "an iron sword",
                "typeclass": "typeclasses.objects.Item",
            }
        )
        item = spawn(self.PROTOTYPE_KEY)[0]
        item.location = self.char1
        item_id = item.id

        self.call(CmdJunk(), "iron sword", "You junk an iron sword.")

        self.assertFalse(ObjectDB.objects.filter(id=item_id).exists())
        self.assertTrue(
            any(
                proto.get("prototype_key") == self.PROTOTYPE_KEY
                for proto in search_prototype(self.PROTOTYPE_KEY)
            )
        )

    def test_requires_an_item_name(self):
        self.call(CmdJunk(), "", "Junk what?")

    def test_does_not_delete_item_from_room(self):
        item = create_object(
            "typeclasses.objects.Item", key="a pebble", location=self.room1
        )
        item_id = item.id

        self.call(CmdJunk(), "pebble", "You aren't carrying pebble.")

        self.assertTrue(ObjectDB.objects.filter(id=item_id).exists())

    def test_rejects_non_item_in_inventory(self):
        carried_object = create_object(
            "typeclasses.objects.Object", key="a strange object", location=self.char1
        )
        object_id = carried_object.id

        self.call(CmdJunk(), "strange object", "You can only junk items.")

        self.assertTrue(ObjectDB.objects.filter(id=object_id).exists())

    def test_registered_in_character_cmdset(self):
        from commands.default_cmdsets import CharacterCmdSet

        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()

        self.assertTrue(any(isinstance(cmd, CmdJunk) for cmd in cmdset.commands))


class TestWear(EvenniaCommandTest):
    """Wear equips carried items in their configured, unoccupied slots."""

    def create_item(
        self, key: str, wear_locations: list[str], location: Any | None = None
    ) -> Any:
        """Create a wearable item in the caller's inventory by default."""
        return create_object(
            "typeclasses.objects.Item",
            key=key,
            location=location or self.char1,
            attributes=(("wear_locations", wear_locations),),
        )

    def test_wears_item_in_its_only_location(self):
        helmet = self.create_item("a helmet", ["head"])

        self.call(CmdWear(), "helmet", "You wear a helmet on your head.")

        self.assertEqual(helmet.db.worn_location, "head")

    def test_paired_location_requires_side(self):
        ring = self.create_item("a silver ring", ["right finger", "left finger"])

        self.call(
            CmdWear(),
            "silver ring",
            "Wear a silver ring on which side, left or right?",
        )

        self.assertIsNone(ring.db.worn_location)

    def test_side_selects_matching_paired_location(self):
        ring = self.create_item("a silver ring", ["right finger", "left finger"])

        self.call(
            CmdWear(),
            "silver ring right",
            "You wear a silver ring on your right finger.",
        )

        self.assertEqual(ring.db.worn_location, "right finger")

    def test_uses_only_open_paired_location_when_side_is_omitted(self):
        old_wristband = self.create_item(
            "an old wristband", ["right wrist", "left wrist"]
        )
        new_wristband = self.create_item(
            "a new wristband", ["right wrist", "left wrist"]
        )
        old_wristband.db.worn_location = "right wrist"

        self.call(
            CmdWear(),
            "new wristband",
            "You wear a new wristband on your left wrist.",
        )

        self.assertEqual(new_wristband.db.worn_location, "left wrist")

    def test_rejects_multiple_location_item_when_all_locations_are_occupied(self):
        right_ring = self.create_item("a ruby ring", ["right finger"])
        left_ring = self.create_item("an emerald ring", ["left finger"])
        new_ring = self.create_item("a silver ring", ["right finger", "left finger"])
        right_ring.db.worn_location = "right finger"
        left_ring.db.worn_location = "left finger"

        self.call(
            CmdWear(),
            "silver ring",
            "You have no open location where you can wear a silver ring.",
        )

        self.assertIsNone(new_ring.db.worn_location)

    def test_exact_location_selects_from_multiple_nonpaired_locations(self):
        scarf = self.create_item("a silk scarf", ["neck", "about"])

        self.call(
            CmdWear(),
            "silk scarf about",
            "You wear a silk scarf about your body.",
        )

        self.assertEqual(scarf.db.worn_location, "about")

    def test_item_name_ending_in_location_is_not_misparsed(self):
        shield = self.create_item("an iron shield", ["shield"])

        self.call(
            CmdWear(),
            "iron shield",
            "You wear an iron shield as your shield.",
        )

        self.assertEqual(shield.db.worn_location, "shield")

    def test_rejects_side_the_item_does_not_support(self):
        bracelet = self.create_item("a bracelet", ["left wrist"])

        self.call(
            CmdWear(),
            "bracelet right",
            "You cannot wear a bracelet on your right side.",
        )

        self.assertIsNone(bracelet.db.worn_location)

    def test_rejects_occupied_location(self):
        old_helmet = self.create_item("an old helmet", ["head"])
        new_helmet = self.create_item("a new helmet", ["head"])
        old_helmet.db.worn_location = "head"

        self.call(
            CmdWear(),
            "new helmet",
            "You are already wearing an old helmet on your head.",
        )

        self.assertIsNone(new_helmet.db.worn_location)

    def test_rejects_item_without_wear_locations(self):
        rock = self.create_item("a rock", [])

        self.call(CmdWear(), "rock", "You cannot wear a rock.")

        self.assertIsNone(rock.db.worn_location)

    def test_only_carried_items_can_be_worn(self):
        helmet = self.create_item("a helmet", ["head"], location=self.room1)

        self.call(CmdWear(), "helmet", "You aren't carrying helmet.")

        self.assertIsNone(helmet.db.worn_location)

    def test_non_item_cannot_be_worn(self):
        create_object("typeclasses.objects.Object", key="a statue", location=self.char1)

        self.call(CmdWear(), "statue", "You can only wear items.")

    def test_requires_item_name(self):
        self.call(CmdWear(), "", "Wear what?")

    def test_equipped_state_clears_when_item_leaves_inventory(self):
        helmet = self.create_item("a helmet", ["head"])
        helmet.db.worn_location = "head"

        helmet.move_to(self.room1, quiet=True, move_type="drop")

        self.assertIsNone(helmet.db.worn_location)

    def test_dropping_primary_armor_immediately_restores_unarmored_ac(self):
        self.char1.db.dexterity = 14
        armor = self.create_item("chain mail", ["body"])
        armor.db.type = "armor"
        armor.db.subtype = "heavy"
        armor.db.base_ac = 16
        armor.db.worn_location = "body"
        self.assertEqual(self.char1.stats.armor_class, 16)

        armor.move_to(self.room1, quiet=True, move_type="drop")

        self.assertIsNone(armor.db.worn_location)
        self.assertEqual(self.char1.stats.armor_class, 12)

    def test_bulk_unequip_clears_every_benefit_for_death_cleanup(self):
        armor = self.create_item("chain mail", ["body"])
        armor.db.type = "armor"
        armor.db.subtype = "heavy"
        armor.db.base_ac = 16
        armor.db.worn_location = "body"
        sword = self.create_item("a sword", ["wield"])
        sword.db.type = "weapon"
        sword.db.damage = "1d8"
        sword.db.worn_location = "wield"

        changed = self.char1.equipment.unequip_all()

        self.assertEqual(set(changed), {armor, sword})
        self.assertIsNone(armor.db.worn_location)
        self.assertIsNone(sword.db.worn_location)
        self.assertEqual(self.char1.stats.attack_profile().name, "unarmed strike")

    def test_untrained_armor_equips_silently_and_remains_effective(self):
        self.char1.db.char_class = "Wizard"
        armor = self.create_item("plate armor", ["body"])
        armor.db.type = "armor"
        armor.db.subtype = "heavy"
        armor.db.base_ac = 18

        self.call(CmdWear(), "plate armor", "You wear plate armor on your body.")

        self.assertEqual(armor.db.worn_location, "body")
        self.assertEqual(self.char1.stats.armor_class, 18)
        self.assertTrue(self.char1.stats.has_untrained_armor)

    def test_untrained_weapon_equips_without_a_proficiency_warning(self):
        self.char1.db.char_class = "Wizard"
        weapon = self.create_item("a greatsword", ["wield"])
        weapon.db.type = "weapon"
        weapon.db.subtype = "slashing"
        weapon.db.damage = "2d6"
        weapon.db.weapon_category = "martial"
        weapon.db.weapon_kind = "greatsword"
        weapon.db.attack_ability = "strength"

        self.call(
            CmdWear(),
            "greatsword",
            "You wear a greatsword as your weapon.",
        )

        self.assertEqual(weapon.db.worn_location, "wield")
        self.assertFalse(self.char1.stats.attack_profile().proficient)

    def test_supported_locations_include_diku_and_added_slots(self):
        expected = {
            "right finger",
            "left finger",
            "neck",
            "back",
            "body",
            "head",
            "legs",
            "feet",
            "hands",
            "arms",
            "shield",
            "about",
            "waist",
            "right wrist",
            "left wrist",
            "wield",
            "hold",
            "right shoulder",
            "left shoulder",
            "right ankle",
            "left ankle",
            "on belt",
        }

        self.assertEqual(set(WEAR_LOCATIONS), expected)

    def test_remove_keyword_match_clears_equipped_state(self):
        helmet = self.create_item("a steel helmet", ["head"])
        helmet.db.worn_location = "head"

        self.call(CmdRemove(), "steel", "You remove a steel helmet.")

        self.assertIsNone(helmet.db.worn_location)
        self.assertEqual(helmet.location, self.char1)

    def test_remove_only_searches_equipped_items(self):
        helmet = self.create_item("a steel helmet", ["head"])

        self.call(CmdRemove(), "helmet", "You are not wearing helmet.")

        self.assertEqual(helmet.location, self.char1)

    def test_remove_requires_item_name(self):
        self.call(CmdRemove(), "", "Remove what?")

    def test_looking_at_character_shows_equipped_but_not_carried_items(self):
        helmet = self.create_item("a steel helmet", ["head"], location=self.char2)
        backpack = self.create_item("a canvas backpack", ["back"], location=self.char2)
        helmet.db.worn_location = "head"

        out = self.call(CmdLook(), self.char2.key)

        self.assertIn("Equipped:", out)
        self.assertIn("a steel helmet (head)", out)
        self.assertNotIn("canvas backpack", out)
        self.assertEqual(backpack.location, self.char2)

    def test_registered_in_character_cmdset(self):
        from commands.default_cmdsets import CharacterCmdSet

        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()

        self.assertTrue(any(isinstance(cmd, CmdWear) for cmd in cmdset.commands))
        self.assertTrue(any(isinstance(cmd, CmdRemove) for cmd in cmdset.commands))

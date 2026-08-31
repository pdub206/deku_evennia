"""
Tests for common game-command overrides (commands/generic.py).

Run from the game/ directory:
    evennia test --settings settings.py commands.tests.test_generic
"""

from commands.generic import CmdInventory, CmdJunk
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import save_prototype, search_prototype
from evennia.prototypes.spawner import spawn
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

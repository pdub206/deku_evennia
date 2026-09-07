"""SRD spell-slot and Pact Magic resource coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.magic_resources import (
    MagicResourceError,
    recover_profile,
    resource_current,
    resource_maximum,
    resource_view,
    spell_slot_options,
    spend_resource,
)


class TestMagicResources(EvenniaTest):
    """Slot resources are derived from progression instead of mutable maxima."""

    def setUp(self):
        super().setUp()
        self.char1.db.char_class = "Wizard"
        self.char1.db.level = 3

    def test_ordinary_spell_slots_follow_the_srd_class_table(self):
        """A full caster has only the slot levels granted by its current level."""
        self.assertEqual(resource_maximum(self.char1, "wizard.spell_slot.1"), 4)
        self.assertEqual(resource_maximum(self.char1, "wizard.spell_slot.2"), 2)
        with self.assertRaises(MagicResourceError):
            resource_maximum(self.char1, "wizard.spell_slot.3")
        self.assertEqual(
            resource_view(self.char1)[-2:],
            (("wizard.spell_slot.1", 4, 4), ("wizard.spell_slot.2", 2, 2)),
        )

    def test_completed_long_rest_profile_restores_ordinary_slots(self):
        """Only an owning rest adapter may restore the declared slot profile."""
        spend_resource(self.char1, "wizard.spell_slot.1", 3)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 1)
        self.assertEqual(recover_profile(self.char1, "short_rest"), ())
        restored = recover_profile(self.char1, "long_rest")
        self.assertIn(("wizard.spell_slot.1", 4), restored)
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 4)

    def test_pact_magic_is_distinct_and_recovers_on_short_rest(self):
        """Warlock Pact Magic never borrows an ordinary spell-slot resource."""
        self.char1.db.char_class = "Warlock"
        self.char1.db.level = 11
        self.assertEqual(resource_maximum(self.char1, "warlock.pact_slot"), 3)
        with self.assertRaises(MagicResourceError):
            resource_maximum(self.char1, "warlock.spell_slot.1")
        spend_resource(self.char1, "warlock.pact_slot", 2)
        restored = recover_profile(self.char1, "short_rest")
        self.assertIn(("warlock.pact_slot", 3), restored)
        self.assertEqual(resource_current(self.char1, "warlock.pact_slot"), 3)

    def test_slot_options_expose_ordinary_upcasting_and_pact_magic(self):
        """Future casts receive legal slot choices without class-specific checks."""
        self.char1.db.char_class = "Wizard"
        self.char1.db.level = 3
        self.assertEqual(spell_slot_options(self.char1, 0), ())
        self.assertEqual(
            [
                (option.resource_key, option.slot_level)
                for option in spell_slot_options(self.char1, 1)
            ],
            [("wizard.spell_slot.1", 1), ("wizard.spell_slot.2", 2)],
        )
        self.assertEqual(spell_slot_options(self.char1, 3), ())

        self.char1.db.char_class = "Warlock"
        self.char1.db.level = 5
        options = spell_slot_options(self.char1, 1)
        self.assertEqual(
            [(option.resource_key, option.slot_level) for option in options],
            [("warlock.pact_slot", 3)],
        )
        self.assertEqual(spell_slot_options(self.char1, 4), ())

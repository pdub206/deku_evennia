"""SRD spell-slot and Pact Magic resource coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.magic_resources import (
    MagicResourceError,
    recover_profile,
    resource_current,
    resource_maximum,
    resource_view,
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

    def test_fighter_resource_is_distinct_and_recovers_on_short_rest(self):
        """A released class resource never borrows a spell-slot resource."""
        self.char1.db.char_class = "Fighter"
        self.char1.db.level = 3
        self.assertEqual(resource_maximum(self.char1, "fighter.second_wind"), 2)
        with self.assertRaises(MagicResourceError):
            resource_maximum(self.char1, "fighter.spell_slot.1")
        spend_resource(self.char1, "fighter.second_wind", 2)
        restored = recover_profile(self.char1, "short_rest")
        self.assertIn(("fighter.second_wind", 2), restored)
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 2)

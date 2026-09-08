"""SRD spell-slot and Pact Magic resource coverage."""

from dataclasses import replace
from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.magic_resources import (MagicResourceError, initialize_resource,
                                     initialize_spell_access_resources,
                                     recover_profile, resource_current,
                                     resource_maximum, resource_view,
                                     spell_slot_options, spend_resource)
from systems.progression import CLASS_PROGRESSION, build_registry
from systems.srd_class_resources import SRD_CLASS_RESOURCES


def _registry_with_released_resource(key: str):
    """Promote one catalogue record solely for resource-service coverage."""
    original = CLASS_PROGRESSION.resources[key]
    released = replace(original, owner="resources", release_state="released")
    resources = list(CLASS_PROGRESSION.resources.values())
    resources[resources.index(original)] = released
    return build_registry(
        CLASS_PROGRESSION.definitions.values(),
        CLASS_PROGRESSION.features.values(),
        resources,
        CLASS_PROGRESSION.spell_access.values(),
        CLASS_PROGRESSION.choices.values(),
    )


class TestMagicResources(EvenniaTest):
    """Slot resources are derived from progression instead of mutable maxima."""

    def setUp(self):
        super().setUp()
        self.char1.db.char_class = "Wizard"
        self.char1.db.level = 3
        self._initialize_slots()

    def _initialize_slots(self):
        """Model the explicit slot grant that direct fixture edits bypass."""
        key = f"{self.char1.db.char_class.casefold()}.spell_access"
        if key in CLASS_PROGRESSION.spell_access:
            initialize_spell_access_resources(self.char1, key)

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

    def test_catalogued_class_resource_cannot_be_spent_or_recovered_early(self):
        """Source records never masquerade as released class-feature mechanics."""
        with self.assertRaises(MagicResourceError):
            resource_maximum(self.char1, "wizard.arcane_recovery")
        self.assertNotIn(
            "wizard.arcane_recovery",
            tuple(key for key, *_ in resource_view(self.char1)),
        )

    def test_each_persistent_source_resource_has_atomic_capacity_and_long_recovery(
        self,
    ):
        """Promotion exercises the declared pool without publishing its feature."""
        for key, source in SRD_CLASS_RESOURCES.items():
            if source.recovery_profile == "per_eligible_attack":
                continue
            registry = _registry_with_released_resource(key)
            self.char1.db.char_class = key.split(".", maxsplit=1)[0].title()
            self.char1.db.level = 20
            self.char1.attributes.remove("magic_resources")
            with patch("systems.magic_resources.CLASS_PROGRESSION", registry):
                spell_key = f"{self.char1.db.char_class.casefold()}.spell_access"
                if spell_key in registry.spell_access:
                    initialize_spell_access_resources(self.char1, spell_key)
                maximum = resource_maximum(self.char1, key)
                self.assertGreater(maximum, 0)
                initialize_resource(self.char1, key)
                self.assertEqual(spend_resource(self.char1, key, 1), maximum - 1)
                self.assertIn((key, maximum), recover_profile(self.char1, "long_rest"))
                self.assertEqual(resource_current(self.char1, key), maximum)

    def test_source_short_rest_profiles_preserve_partial_and_level_gated_recovery(self):
        """Rage and Bardic Inspiration retain their distinct short-rest rules."""
        self.char1.db.char_class = "Barbarian"
        self.char1.db.level = 1
        with patch(
            "systems.magic_resources.CLASS_PROGRESSION",
            _registry_with_released_resource("barbarian.rage"),
        ):
            initialize_resource(self.char1, "barbarian.rage")
            spend_resource(self.char1, "barbarian.rage", 2)
            self.assertEqual(
                recover_profile(self.char1, "short_rest"), (("barbarian.rage", 1),)
            )
            self.assertEqual(resource_current(self.char1, "barbarian.rage"), 1)

        self.char1.attributes.remove("magic_resources")
        self.char1.db.char_class = "Bard"
        self.char1.db.charisma = 14
        with patch(
            "systems.magic_resources.CLASS_PROGRESSION",
            _registry_with_released_resource("bard.bardic_inspiration"),
        ):
            self.char1.db.level = 4
            self._initialize_slots()
            maximum = resource_maximum(self.char1, "bard.bardic_inspiration")
            initialize_resource(self.char1, "bard.bardic_inspiration")
            spend_resource(self.char1, "bard.bardic_inspiration", 1)
            self.assertEqual(recover_profile(self.char1, "short_rest"), ())
            self.char1.db.level = 5
            self.assertEqual(
                recover_profile(self.char1, "short_rest"),
                (("bard.bardic_inspiration", maximum),),
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
        self._initialize_slots()
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
        self._initialize_slots()
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
        self._initialize_slots()
        options = spell_slot_options(self.char1, 1)
        self.assertEqual(
            [(option.resource_key, option.slot_level) for option in options],
            [("warlock.pact_slot", 3)],
        )
        self.assertEqual(spell_slot_options(self.char1, 4), ())

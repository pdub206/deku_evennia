"""ADV-02 class progression registry coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.progression import (
    CLASS_PROGRESSION,
    MAX_CLASS_LEVEL,
    SELECTABLE_CLASS_NAMES,
    RegistryValidationError,
    build_registry,
)
from systems.srd_class_features import SRD_CLASS_FEATURES, SRD_SUBCLASS_FEATURES


class TestClassProgressionRegistry(EvenniaTest):
    """Every selectable class must resolve through the one validated registry."""

    def test_all_selectable_classes_have_twenty_complete_levels(self):
        self.assertEqual(tuple(CLASS_PROGRESSION.definitions), SELECTABLE_CLASS_NAMES)
        for class_key in SELECTABLE_CLASS_NAMES:
            definition = CLASS_PROGRESSION.class_for(class_key)
            self.assertEqual(len(definition.levels), MAX_CLASS_LEVEL)
            self.assertEqual(
                tuple(grants.level for grants in definition.levels),
                tuple(range(1, MAX_CLASS_LEVEL + 1)),
            )
            self.assertEqual(
                CLASS_PROGRESSION.chargen_summary(class_key)["hit_die"],
                definition.hit_die,
            )
            self.assertTrue(definition.srd_reference.startswith("SRD 5.2.1 "))
            self.assertIn(class_key, definition.srd_reference)

    def test_cited_catalogue_matches_every_source_feature_level(self):
        """Source data is visible to progression without claiming mechanics work."""
        for definition in CLASS_PROGRESSION.definitions.values():
            first = definition.grants_at(1)
            self.assertIn(definition.skill_choice_key, first.choice_keys)
            self.assertEqual(first.automatic_feature_keys, ())
            table = SRD_CLASS_FEATURES[definition.key]
            for level in range(1, MAX_CLASS_LEVEL + 1):
                grants = definition.grants_at(level)
                entries = tuple(
                    CLASS_PROGRESSION.features[key]
                    for key in grants.catalogued_feature_keys
                )
                self.assertEqual(
                    tuple(entry.display_name for entry in entries),
                    table.features_at(level),
                )
                self.assertTrue(
                    all(entry.release_state == "catalogued" for entry in entries)
                )
                self.assertTrue(
                    all(entry.srd_reference == table.srd_reference for entry in entries)
                )
                self.assertTrue(
                    all(entry.feature_shape != "unclassified" for entry in entries)
                )
                self.assertTrue(all(entry.release_adapter for entry in entries))

    def test_catalogued_subclasses_require_selection_without_granting_mechanics(self):
        """Subclass source data records its dependency but is not selectable yet."""
        for definition in CLASS_PROGRESSION.definitions.values():
            table = SRD_SUBCLASS_FEATURES[definition.key]
            selection = definition.grants_at(3).catalogued_subclass_choice_keys
            self.assertEqual(len(selection), 1)
            selection_key = selection[0]
            selection_definition = CLASS_PROGRESSION.features[selection_key]
            self.assertEqual(selection_definition.display_name, table.subclass_name)
            self.assertEqual(selection_definition.grant_mode, "choice")
            self.assertEqual(selection_definition.feature_shape, "choice")
            self.assertEqual(selection_definition.release_state, "catalogued")
            self.assertEqual(selection_definition.srd_reference, table.srd_reference)
            for level in range(1, MAX_CLASS_LEVEL + 1):
                grants = definition.grants_at(level)
                entries = tuple(
                    CLASS_PROGRESSION.features[key]
                    for key in grants.catalogued_subclass_feature_keys
                )
                self.assertEqual(
                    tuple(entry.display_name for entry in entries),
                    table.features_at(level),
                )
                self.assertTrue(
                    all(entry.prerequisites == (selection_key,) for entry in entries)
                )
                self.assertTrue(
                    all(entry.feature_shape != "unclassified" for entry in entries)
                )
                self.assertTrue(all(entry.release_adapter for entry in entries))

    def test_srd_spell_access_tables_retain_slots_and_pact_magic_separately(self):
        """Slot counts use their class tables rather than generic resource curves."""
        bard = CLASS_PROGRESSION.spell_access["bard.spell_access"]
        self.assertEqual(bard.cantrips[0], 2)
        self.assertEqual(bard.spells_prepared[0], 4)
        self.assertEqual(bard.spell_slots[0][0], 2)
        self.assertEqual(bard.spell_slots[1][2], 2)
        self.assertEqual(bard.spell_slots[8][16], 1)
        self.assertEqual(bard.pact_slots, (0,) * MAX_CLASS_LEVEL)

        paladin = CLASS_PROGRESSION.spell_access["paladin.spell_access"]
        self.assertEqual(paladin.spell_slots[0][0], 2)
        self.assertEqual(paladin.spell_slots[1][4], 2)
        self.assertEqual(paladin.spell_slots[4][16], 1)

        warlock = CLASS_PROGRESSION.spell_access["warlock.spell_access"]
        self.assertEqual(warlock.spell_slots, ((0,) * MAX_CLASS_LEVEL,) * 9)
        self.assertEqual(warlock.pact_slots[0], 1)
        self.assertEqual(warlock.pact_slots[10], 3)
        self.assertEqual(warlock.pact_slot_level[8], 5)
        self.assertEqual(warlock.maximum_spell_level[10], 6)
        self.assertTrue(warlock.srd_reference.startswith("SRD 5.2.1 "))

    def test_registry_projection_is_immutable_and_deterministic(self):
        summaries = CLASS_PROGRESSION.chargen_summaries()
        with self.assertRaises(TypeError):
            summaries["Fighter"] = {}  # type: ignore[index]
        self.assertEqual(
            CLASS_PROGRESSION.fingerprint,
            CLASS_PROGRESSION.fingerprint,
        )
        self.assertEqual(CLASS_PROGRESSION.version, 6)

    def test_invalid_level_gap_and_unknown_feature_fail_closed(self):
        fighter = CLASS_PROGRESSION.class_for("Fighter")
        incomplete = replace(fighter, levels=fighter.levels[:-1])
        definitions = list(CLASS_PROGRESSION.definitions.values())
        definitions[definitions.index(fighter)] = incomplete
        with self.assertRaises(RegistryValidationError):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        invalid_reference = replace(fighter, srd_reference="unreviewed source")
        definitions[definitions.index(incomplete)] = invalid_reference
        with self.assertRaises(RegistryValidationError):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        bad_level = replace(
            fighter.grants_at(1), automatic_feature_keys=("missing.feature",)
        )
        invalid = replace(fighter, levels=(bad_level,) + fighter.levels[1:])
        definitions[definitions.index(invalid_reference)] = invalid
        with self.assertRaises(RegistryValidationError):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        catalogued_key = fighter.grants_at(1).catalogued_feature_keys[0]
        unreleased_grant = replace(
            fighter.grants_at(1), automatic_feature_keys=(catalogued_key,)
        )
        unreleased = replace(fighter, levels=(unreleased_grant,) + fighter.levels[1:])
        definitions[definitions.index(invalid)] = unreleased
        with self.assertRaises(RegistryValidationError):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        catalogued = CLASS_PROGRESSION.features[catalogued_key]
        adapterless_release = replace(
            catalogued,
            owner="advancement",
            release_state="released",
            release_adapter="",
        )
        features = list(CLASS_PROGRESSION.features.values())
        features[features.index(catalogued)] = adapterless_release
        with self.assertRaises(RegistryValidationError):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                features,
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        skill_choice = CLASS_PROGRESSION.choices[fighter.skill_choice_key]
        uncited_choice = replace(skill_choice, srd_reference="")
        choices = list(CLASS_PROGRESSION.choices.values())
        choices[choices.index(skill_choice)] = uncited_choice
        with self.assertRaises(RegistryValidationError):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                choices,
            )

        catalogued_choice = replace(skill_choice, release_state="catalogued")
        choices[choices.index(uncited_choice)] = catalogued_choice
        with self.assertRaises(RegistryValidationError):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                choices,
            )

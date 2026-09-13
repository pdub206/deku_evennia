"""ADV-02 class progression registry coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.progression import (
    CLASS_PROGRESSION,
    MAX_CLASS_LEVEL,
    SELECTABLE_CLASS_NAMES,
    UNAVAILABLE_CLASS_NAMES,
    RegistryValidationError,
    build_registry,
)


class TestClassProgressionRegistry(EvenniaTest):
    """Every selectable class must resolve through the one validated registry."""

    def test_all_selectable_classes_have_three_complete_levels(self):
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

    def test_every_declared_grant_key_resolves(self):
        for definition in CLASS_PROGRESSION.definitions.values():
            self.assertIn(
                definition.skill_choice_key, definition.grants_at(1).choice_keys
            )
            for grants in definition.levels:
                for key in grants.automatic_feature_keys:
                    self.assertIn(key, CLASS_PROGRESSION.features)
                for key in grants.resource_keys:
                    self.assertIn(key, CLASS_PROGRESSION.resources)
                for key in grants.spell_access_keys:
                    self.assertIn(key, CLASS_PROGRESSION.spell_access)
                for key in grants.choice_keys:
                    self.assertIn(key, CLASS_PROGRESSION.choices)

    def test_alpha_spell_access_matches_cleric_and_wizard_tables(self):
        for key in ("cleric.spell_access", "wizard.spell_access"):
            access = CLASS_PROGRESSION.spell_access[key]
            self.assertEqual(access.cantrips, (3, 3, 3))
            self.assertEqual(access.spells_prepared, (4, 5, 6))
            self.assertEqual(access.maximum_spell_level, (1, 1, 2))
            self.assertEqual(access.spell_slots[0], (2, 3, 4))
            self.assertEqual(access.spell_slots[1], (0, 0, 2))
        self.assertEqual(
            CLASS_PROGRESSION.spell_access["wizard.spell_access"].spellbook_entries,
            (6, 8, 10),
        )

    def test_unreleased_classes_are_unavailable(self):
        for key in UNAVAILABLE_CLASS_NAMES:
            self.assertFalse(CLASS_PROGRESSION.is_available(key))
            with self.assertRaises(RegistryValidationError):
                CLASS_PROGRESSION.class_for(key)

    def test_registry_projection_is_immutable_and_deterministic(self):
        summaries = CLASS_PROGRESSION.chargen_summaries()
        with self.assertRaises(TypeError):
            summaries["Fighter"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            summaries["Fighter"]["armor_training"] = ()  # type: ignore[index]
        self.assertIsInstance(summaries["Fighter"]["armor_training"], tuple)
        self.assertEqual(
            CLASS_PROGRESSION.fingerprint,
            CLASS_PROGRESSION.fingerprint,
        )
        self.assertEqual(CLASS_PROGRESSION.version, 3)

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

    def test_version_repeated_grant_and_prerequisite_order_fail_closed(self):
        """Identity and grant provenance cannot be ambiguous or out of order."""
        with self.assertRaisesRegex(RegistryValidationError, "version"):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
                version=0,
            )

        fighter = CLASS_PROGRESSION.class_for("Fighter")
        duplicate = replace(
            fighter.grants_at(2),
            automatic_feature_keys=("fighter.second_wind",),
        )
        definitions = list(CLASS_PROGRESSION.definitions.values())
        definitions[definitions.index(fighter)] = replace(
            fighter, levels=(fighter.levels[0], duplicate, fighter.levels[2])
        )
        with self.assertRaisesRegex(RegistryValidationError, "repeats"):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

        premature = replace(
            fighter.grants_at(3),
            automatic_feature_keys=(
                "fighter.improved_critical",
                "fighter.champion",
                "fighter.remarkable_athlete",
            ),
        )
        definitions = list(CLASS_PROGRESSION.definitions.values())
        definitions[definitions.index(fighter)] = replace(
            fighter, levels=fighter.levels[:2] + (premature,)
        )
        with self.assertRaisesRegex(RegistryValidationError, "prerequisite"):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

    def test_invalid_choice_exclusions_and_orphan_definitions_fail_closed(self):
        """Choice policy and every registered definition belong to the graph."""
        choices = list(CLASS_PROGRESSION.choices.values())
        fighter_skills = CLASS_PROGRESSION.choices["fighter.skills"]
        choices[choices.index(fighter_skills)] = replace(
            fighter_skills,
            mutual_exclusions=(("Athletics", "not-an-option"),),
        )
        with self.assertRaisesRegex(RegistryValidationError, "mutual exclusions"):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                choices,
            )

        orphan = replace(
            fighter_skills,
            key="fighter.unreferenced_choice",
            mutual_exclusions=(),
        )
        with self.assertRaisesRegex(RegistryValidationError, "unreferenced"):
            build_registry(
                CLASS_PROGRESSION.definitions.values(),
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                (*CLASS_PROGRESSION.choices.values(), orphan),
            )

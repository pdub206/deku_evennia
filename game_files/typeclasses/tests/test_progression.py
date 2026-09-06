"""ADV-02 class progression registry coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.progression import (CLASS_PROGRESSION, MAX_CLASS_LEVEL,
                                 SELECTABLE_CLASS_NAMES,
                                 RegistryValidationError, build_registry)


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

    def test_level_one_choice_and_repeated_feature_keys_resolve(self):
        for definition in CLASS_PROGRESSION.definitions.values():
            first = definition.grants_at(1)
            self.assertIn(definition.skill_choice_key, first.choice_keys)
            self.assertTrue(first.automatic_feature_keys)
            self.assertEqual(
                first.automatic_feature_keys,
                definition.grants_at(MAX_CLASS_LEVEL).automatic_feature_keys,
            )
            self.assertEqual(
                CLASS_PROGRESSION.features[first.automatic_feature_keys[0]].repeat_mode,
                "upgrade",
            )

    def test_registry_projection_is_immutable_and_deterministic(self):
        summaries = CLASS_PROGRESSION.chargen_summaries()
        with self.assertRaises(TypeError):
            summaries["Fighter"] = {}  # type: ignore[index]
        self.assertEqual(
            CLASS_PROGRESSION.fingerprint,
            CLASS_PROGRESSION.fingerprint,
        )

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

        bad_level = replace(
            fighter.grants_at(1), automatic_feature_keys=("missing.feature",)
        )
        invalid = replace(fighter, levels=(bad_level,) + fighter.levels[1:])
        definitions[definitions.index(incomplete)] = invalid
        with self.assertRaises(RegistryValidationError):
            build_registry(
                definitions,
                CLASS_PROGRESSION.features.values(),
                CLASS_PROGRESSION.resources.values(),
                CLASS_PROGRESSION.spell_access.values(),
                CLASS_PROGRESSION.choices.values(),
            )

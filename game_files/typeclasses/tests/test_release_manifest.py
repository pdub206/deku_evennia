"""MAGIC-03 alpha release-manifest coverage."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.magic import MAGIC_REGISTRY
from systems.progression import CLASS_PROGRESSION, SELECTABLE_CLASS_NAMES
from systems.release_manifest import (ALPHA_MANIFEST_VERSION,
                                      ALPHA_RELEASE_MANIFEST,
                                      ReleaseManifestError,
                                      build_alpha_manifest)


class TestAlphaReleaseManifest(EvenniaTest):
    """The release census binds selectable progression to executable magic."""

    def test_manifest_identifies_exact_classes_subclasses_and_registries(self):
        """The immutable projection publishes only the four level-three kits."""
        self.assertEqual(tuple(ALPHA_RELEASE_MANIFEST.classes), SELECTABLE_CLASS_NAMES)
        self.assertEqual(ALPHA_RELEASE_MANIFEST.version, ALPHA_MANIFEST_VERSION)
        self.assertEqual(
            ALPHA_RELEASE_MANIFEST.progression_fingerprint,
            CLASS_PROGRESSION.fingerprint,
        )
        self.assertEqual(
            ALPHA_RELEASE_MANIFEST.magic_fingerprint, MAGIC_REGISTRY.fingerprint
        )
        self.assertEqual(
            tuple(
                released.subclass
                for released in ALPHA_RELEASE_MANIFEST.classes.values()
            ),
            ("Life Domain", "Champion", "Thief", "Evoker"),
        )
        self.assertTrue(
            all(
                released.level_cap == 3
                for released in ALPHA_RELEASE_MANIFEST.classes.values()
            )
        )

    def test_every_progression_grant_and_magic_action_appears_once(self):
        """No selectable grant or class action exists outside the census."""
        manifest_actions: list[str] = []
        for class_key, released in ALPHA_RELEASE_MANIFEST.classes.items():
            definition = CLASS_PROGRESSION.class_for(class_key)
            self.assertEqual(
                set(released.feature_keys),
                {
                    key
                    for level in definition.levels
                    for key in level.automatic_feature_keys
                },
            )
            manifest_actions.extend(released.magic_action_keys)
        self.assertEqual(set(manifest_actions), set(MAGIC_REGISTRY.definitions))
        self.assertEqual(len(manifest_actions), len(set(manifest_actions)))

    def test_manifest_carries_every_runtime_dependency_and_reference(self):
        """The census is sufficient to audit execution, effects, help, and SRD data."""
        expected_tactics = {
            "Cleric": (),
            "Fighter": ("aim", "bash", "kick"),
            "Rogue": ("aim", "backstab", "hide", "steady_aim"),
            "Wizard": (),
        }
        for class_key, released in ALPHA_RELEASE_MANIFEST.classes.items():
            actions = tuple(
                MAGIC_REGISTRY.definitions[key] for key in released.magic_action_keys
            )
            self.assertEqual(
                released.spell_access_keys,
                tuple(
                    dict.fromkeys(
                        key
                        for level in CLASS_PROGRESSION.class_for(class_key).levels
                        for key in level.spell_access_keys
                    )
                ),
            )
            self.assertEqual(released.tactical_action_keys, expected_tactics[class_key])
            self.assertEqual(
                set(released.effect_keys),
                {key for action in actions for key in action.effect_keys},
            )
            self.assertEqual(
                set(released.handler_keys), {action.handler_key for action in actions}
            )
            self.assertEqual(
                set(released.adaptation_action_keys),
                {
                    action.key
                    for action in actions
                    if any(tag.startswith("alpha_") for tag in action.tags)
                },
            )
            self.assertIn("class progression", released.help_keys)
            self.assertTrue(
                {action.player_help.key for action in actions}
                <= set(released.help_keys)
            )
            self.assertTrue(released.srd_references)
            self.assertTrue(
                all(
                    reference.startswith("SRD 5.2.1 ")
                    for reference in released.srd_references
                )
            )

    def test_caster_catalogs_fill_level_three_choices_and_spell_level(self):
        """Both casters can fill their released preparation ownership limits."""
        for class_key in ("Cleric", "Wizard"):
            released = ALPHA_RELEASE_MANIFEST.classes[class_key]
            definitions = tuple(
                MAGIC_REGISTRY.definitions[key] for key in released.magic_action_keys
            )
            access = CLASS_PROGRESSION.spell_access[
                f"{class_key.casefold()}.spell_access"
            ]
            self.assertGreaterEqual(
                sum(action.spell_level == 0 for action in definitions),
                access.cantrips[2],
            )
            self.assertGreaterEqual(
                sum(action.spell_level > 0 for action in definitions),
                max(access.spells_prepared[2], access.spellbook_entries[2]),
            )
            self.assertTrue(
                any(
                    action.spell_level == access.maximum_spell_level[2]
                    for action in definitions
                )
            )

    def test_four_class_three_level_matrix_is_cumulative_and_actionable(self):
        """All twelve release rows resolve grants and at least one live action."""
        rows = 0
        for class_key, released in ALPHA_RELEASE_MANIFEST.classes.items():
            self.assertEqual(tuple(row.level for row in released.levels), (1, 2, 3))
            previous_features: set[str] = set()
            for row in released.levels:
                rows += 1
                self.assertTrue(row.magic_action_keys or row.tactical_action_keys)
                self.assertTrue(previous_features <= set(row.feature_keys))
                previous_features = set(row.feature_keys)
                self.assertTrue(
                    set(row.feature_keys) <= set(CLASS_PROGRESSION.features)
                )
                self.assertTrue(
                    set(row.resource_keys) <= set(CLASS_PROGRESSION.resources)
                )
                self.assertTrue(set(row.choice_keys) <= set(CLASS_PROGRESSION.choices))
                self.assertTrue(
                    set(row.spell_access_keys) <= set(CLASS_PROGRESSION.spell_access)
                )
        self.assertEqual(rows, 12)

    def test_released_standard_arrays_prioritize_each_class_engine(self):
        """The authoritative manifest rejects a chargen array that strands a kit."""
        from world import chargen_data

        invalid = dict(chargen_data.STANDARD_ARRAY_BY_CLASS)
        invalid["Wizard"] = {
            **invalid["Wizard"],
            "Intelligence": 8,
            "Strength": 15,
        }
        with (
            patch.object(chargen_data, "STANDARD_ARRAY_BY_CLASS", invalid),
            self.assertRaisesRegex(ReleaseManifestError, "standard-array"),
        ):
            build_alpha_manifest()

    def test_manifest_rejects_an_incomplete_released_spell_catalog(self):
        """Removing one required Wizard spell makes the release fail closed."""
        definitions = dict(MAGIC_REGISTRY.definitions)
        definitions.pop("wizard.blur")
        incomplete_magic = SimpleNamespace(
            definitions=MappingProxyType(definitions),
            version=MAGIC_REGISTRY.version,
            fingerprint="incomplete",
        )

        with self.assertRaisesRegex(
            ReleaseManifestError, "Wizard lacks enough released spells"
        ):
            build_alpha_manifest(CLASS_PROGRESSION, incomplete_magic)

    def test_manifest_rejects_missing_help_or_tactical_handlers(self):
        """A class cannot remain selectable with a broken player/runtime edge."""
        from world import help_entries

        published = tuple(
            entry
            for entry in help_entries.HELP_ENTRY_DICTS
            if entry.get("key") != "fire bolt"
        )
        with (
            patch.object(help_entries, "HELP_ENTRY_DICTS", published),
            self.assertRaisesRegex(ReleaseManifestError, "unpublished player help"),
        ):
            build_alpha_manifest()

        with (
            patch("systems.tactical_combat.TACTICAL_ACTIONS.get", return_value=None),
            self.assertRaisesRegex(ReleaseManifestError, "tactical action"),
        ):
            build_alpha_manifest()

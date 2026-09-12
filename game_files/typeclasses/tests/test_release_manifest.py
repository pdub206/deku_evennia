"""MAGIC-03 alpha release-manifest coverage."""

from types import MappingProxyType, SimpleNamespace

from evennia.utils.test_resources import EvenniaTest
from systems.magic import MAGIC_REGISTRY
from systems.progression import CLASS_PROGRESSION, SELECTABLE_CLASS_NAMES
from systems.release_manifest import (
    ALPHA_MANIFEST_VERSION,
    ALPHA_RELEASE_MANIFEST,
    ReleaseManifestError,
    build_alpha_manifest,
)


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

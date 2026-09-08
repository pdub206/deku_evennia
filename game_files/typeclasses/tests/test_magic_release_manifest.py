"""P-05 fail-closed class-kit release-manifest coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.magic_release_manifest import (
    MAGIC_03_RELEASE_MANIFEST,
    MAGIC_RELEASE_MANIFEST_VERSION,
    NORMAL_LEVEL_CAP,
    ReleaseManifestError,
    build_release_manifest,
    class_level_coverage,
)
from systems.progression import SELECTABLE_CLASS_NAMES


class TestMagicReleaseManifest(EvenniaTest):
    """Published kits cannot outrun the independently released P-04 entries."""

    def test_empty_manifest_is_versioned_and_does_not_claim_a_class_kit(self):
        self.assertEqual(
            MAGIC_03_RELEASE_MANIFEST.version, MAGIC_RELEASE_MANIFEST_VERSION
        )
        self.assertEqual(MAGIC_03_RELEASE_MANIFEST.level_cap, NORMAL_LEVEL_CAP)
        self.assertEqual(dict(MAGIC_03_RELEASE_MANIFEST.published_class_levels), {})
        self.assertTrue(
            MAGIC_03_RELEASE_MANIFEST.srd_reference.startswith("SRD 5.2.1 ")
        )
        with self.assertRaises(TypeError):
            MAGIC_03_RELEASE_MANIFEST.published_class_levels["Fighter"] = ()

    def test_matrix_covers_every_source_class_and_level(self):
        matrix = tuple(
            class_level_coverage(class_key, level)
            for class_key in SELECTABLE_CLASS_NAMES
            for level in range(1, NORMAL_LEVEL_CAP + 1)
        )

        self.assertEqual(len(matrix), len(SELECTABLE_CLASS_NAMES) * NORMAL_LEVEL_CAP)
        barbarian = next(
            item for item in matrix if item.class_key == "Barbarian" and item.level == 1
        )
        self.assertIn("barbarian.unarmored_defense", barbarian.released_feature_keys)
        self.assertIn("catalogued_feature:barbarian.rage", barbarian.blockers)
        fighter = next(
            item for item in matrix if item.class_key == "Fighter" and item.level == 1
        )
        self.assertIn("fighter.second_wind", fighter.released_feature_keys)
        self.assertIn("fighter.second_wind", fighter.available_action_keys)
        self.assertFalse(fighter.playable)
        wizard = next(
            item for item in matrix if item.class_key == "Wizard" and item.level == 1
        )
        self.assertIn("missing_spell_action", wizard.blockers)

    def test_incomplete_or_partial_class_band_cannot_be_published(self):
        with self.assertRaisesRegex(
            ReleaseManifestError, "every level from 1 through 20"
        ):
            build_release_manifest({"Fighter": (1,)})
        with self.assertRaisesRegex(ReleaseManifestError, "level 1 is incomplete"):
            build_release_manifest({"Fighter": tuple(range(1, NORMAL_LEVEL_CAP + 1))})

    def test_invalid_cap_and_coverage_inputs_fail_closed(self):
        with self.assertRaisesRegex(ReleaseManifestError, "exactly 20"):
            build_release_manifest({}, level_cap=3)
        with self.assertRaises(ReleaseManifestError):
            class_level_coverage("Fighter", 21)
        with self.assertRaises(ReleaseManifestError):
            class_level_coverage("Unknown", 1)

"""SRD 5.2.1 class-feature table source-data coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.progression import MAX_CLASS_LEVEL, SELECTABLE_CLASS_NAMES
from systems.srd_class_features import SRD_CLASS_FEATURES, SRD_SUBCLASS_FEATURES


class TestSRDClassFeatureTables(EvenniaTest):
    """The local SRD tables cover every supported class and level exactly once."""

    def test_all_classes_have_cited_twenty_level_feature_tables(self):
        self.assertEqual(tuple(SRD_CLASS_FEATURES), SELECTABLE_CLASS_NAMES)
        for class_key, table in SRD_CLASS_FEATURES.items():
            self.assertEqual(table.class_key, class_key)
            self.assertEqual(len(table.level_features), MAX_CLASS_LEVEL)
            self.assertTrue(table.srd_reference.startswith("SRD 5.2.1 p."))

    def test_representative_srd_grants_are_source_controlled(self):
        self.assertEqual(
            SRD_CLASS_FEATURES["Barbarian"].features_at(1),
            ("Rage", "Unarmored Defense", "Weapon Mastery"),
        )
        self.assertEqual(
            SRD_CLASS_FEATURES["Fighter"].features_at(17),
            ("Action Surge", "Indomitable"),
        )
        self.assertEqual(
            SRD_CLASS_FEATURES["Warlock"].features_at(17), ("Mystic Arcanum",)
        )
        self.assertEqual(
            SRD_CLASS_FEATURES["Wizard"].features_at(20), ("Signature Spells",)
        )

    def test_invalid_level_does_not_produce_a_partial_feature_lookup(self):
        with self.assertRaises(KeyError):
            SRD_CLASS_FEATURES["Cleric"].features_at(0)
        with self.assertRaises(KeyError):
            SRD_CLASS_FEATURES["Cleric"].features_at(21)

    def test_every_class_has_one_cited_srd_subclass_table(self):
        """The SRD's included subclass is catalogued for each base class."""
        self.assertEqual(tuple(SRD_SUBCLASS_FEATURES), SELECTABLE_CLASS_NAMES)
        for class_key, table in SRD_SUBCLASS_FEATURES.items():
            self.assertEqual(table.class_key, class_key)
            self.assertEqual(len(table.level_features), MAX_CLASS_LEVEL)
            self.assertTrue(table.subclass_name)
            self.assertTrue(table.srd_reference.startswith("SRD 5.2.1 p."))

        self.assertEqual(
            SRD_SUBCLASS_FEATURES["Wizard"].features_at(14), ("Overchannel",)
        )

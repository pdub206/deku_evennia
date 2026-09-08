"""P04-A01 immutable source-census coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.srd_content_census import (SRD_CONTENT_CENSUS, ContentCensusError,
                                        build_content_census, census_report)
from systems.srd_spell_lists import (SRD_CANTRIP_LISTS,
                                     SRD_LEVEL_EIGHT_SPELL_LISTS,
                                     SRD_LEVEL_FIVE_SPELL_LISTS,
                                     SRD_LEVEL_FOUR_SPELL_LISTS,
                                     SRD_LEVEL_NINE_SPELL_LISTS,
                                     SRD_LEVEL_ONE_SPELL_LISTS,
                                     SRD_LEVEL_SEVEN_SPELL_LISTS,
                                     SRD_LEVEL_SIX_SPELL_LISTS,
                                     SRD_LEVEL_THREE_SPELL_LISTS,
                                     SRD_LEVEL_TWO_SPELL_LISTS)


class TestSRDContentCensus(EvenniaTest):
    """Every authored progression row has one accountable P-04 record."""

    def test_census_is_immutable_and_covers_current_source_projection(self):
        report = census_report()

        self.assertEqual(SRD_CONTENT_CENSUS.version, 1)
        self.assertEqual(len(SRD_CONTENT_CENSUS.records), 1312)
        self.assertEqual(len(report["released"]), 3)
        self.assertIn("feature:fighter.second_wind:level:1", report["released"])
        self.assertIn("feature:barbarian.rage:level:1", report["catalogued"])
        self.assertIn("feat:magic_initiate", report["catalogued"])
        self.assertIn("equipment:weapon:longbow", report["catalogued"])
        self.assertIn("equipment:armor:plate_armor", report["catalogued"])
        self.assertIn("spell:wizard:fire_bolt:level:0", report["catalogued"])
        self.assertIn("spell:warlock:hex:level:1", report["catalogued"])
        self.assertIn("spell:wizard:acid_arrow:level:2", report["catalogued"])
        self.assertIn("spell:sorcerer:fireball:level:3", report["catalogued"])
        self.assertIn("spell:wizard:arcane_eye:level:4", report["catalogued"])
        self.assertIn("spell:wizard:summon_dragon:level:5", report["catalogued"])
        self.assertIn("spell:wizard:wall_of_ice:level:6", report["catalogued"])
        self.assertIn("spell:wizard:simulacrum:level:7", report["catalogued"])
        self.assertIn("spell:wizard:maze:level:8", report["catalogued"])
        self.assertIn("spell:wizard:wish:level:9", report["catalogued"])

    def test_cantrip_source_rows_cover_all_eight_class_lists(self):
        """P04-S00 preserves list membership even for shared cantrip names."""
        self.assertEqual(
            tuple(SRD_CANTRIP_LISTS),
            (
                "Bard",
                "Cleric",
                "Druid",
                "Paladin",
                "Ranger",
                "Sorcerer",
                "Warlock",
                "Wizard",
            ),
        )
        self.assertEqual(sum(len(entries) for entries in SRD_CANTRIP_LISTS.values()), 65)
        self.assertEqual(SRD_CANTRIP_LISTS["Paladin"], ())
        self.assertEqual(SRD_CANTRIP_LISTS["Ranger"], ())

    def test_level_one_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S01 retains per-class ownership for shared level-one spells."""
        self.assertEqual(tuple(SRD_LEVEL_ONE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_ONE_SPELL_LISTS.values()), 145
        )
        self.assertEqual(SRD_LEVEL_ONE_SPELL_LISTS["Bard"][0].spell_name, "Animal Friendship")
        self.assertEqual(SRD_LEVEL_ONE_SPELL_LISTS["Wizard"][-1].spell_name, "Unseen Servant")

    def test_level_two_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S02 retains all source memberships before mechanics exist."""
        self.assertEqual(tuple(SRD_LEVEL_TWO_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_TWO_SPELL_LISTS.values()), 156
        )
        self.assertEqual(SRD_LEVEL_TWO_SPELL_LISTS["Bard"][0].spell_name, "Aid")
        self.assertEqual(SRD_LEVEL_TWO_SPELL_LISTS["Wizard"][-1].spell_name, "Web")

    def test_level_three_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S03 preserves spell-list membership for every level-three row."""
        self.assertEqual(tuple(SRD_LEVEL_THREE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_THREE_SPELL_LISTS.values()), 128
        )
        self.assertEqual(SRD_LEVEL_THREE_SPELL_LISTS["Bard"][0].spell_name, "Bestow Curse")
        self.assertEqual(SRD_LEVEL_THREE_SPELL_LISTS["Wizard"][-1].spell_name, "Water Breathing")

    def test_level_four_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S04 preserves spell-list membership for every level-four row."""
        self.assertEqual(tuple(SRD_LEVEL_FOUR_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_FOUR_SPELL_LISTS.values()), 90
        )
        self.assertEqual(SRD_LEVEL_FOUR_SPELL_LISTS["Bard"][0].spell_name, "Charm Monster")
        self.assertEqual(SRD_LEVEL_FOUR_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Fire")

    def test_level_five_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S05 preserves spell-list membership for every level-five row."""
        self.assertEqual(tuple(SRD_LEVEL_FIVE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_FIVE_SPELL_LISTS.values()), 95
        )
        self.assertEqual(SRD_LEVEL_FIVE_SPELL_LISTS["Bard"][0].spell_name, "Animate Objects")
        self.assertEqual(SRD_LEVEL_FIVE_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Stone")

    def test_level_six_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S06 preserves spell-list membership for every level-six row."""
        self.assertEqual(tuple(SRD_LEVEL_SIX_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_SIX_SPELL_LISTS.values()), 63
        )
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Ranger"], ())
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Bard"][0].spell_name, "Eyebite")
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Ice")

    def test_level_seven_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S07 preserves spell-list membership for every level-seven row."""
        self.assertEqual(tuple(SRD_LEVEL_SEVEN_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_SEVEN_SPELL_LISTS.values()), 52
        )
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Ranger"], ())
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Bard"][0].spell_name, "Arcane Sword")
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Wizard"][-1].spell_name, "Teleport")

    def test_level_eight_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S08 preserves spell-list membership for every level-eight row."""
        self.assertEqual(tuple(SRD_LEVEL_EIGHT_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_EIGHT_SPELL_LISTS.values()), 42
        )
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Ranger"], ())
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Bard"][0].spell_name, "Antipathy/Sympathy")
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Wizard"][-1].spell_name, "Sunburst")

    def test_level_nine_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S09 preserves spell-list membership for every level-nine row."""
        self.assertEqual(tuple(SRD_LEVEL_NINE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_NINE_SPELL_LISTS.values()), 38
        )
        self.assertEqual(SRD_LEVEL_NINE_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_NINE_SPELL_LISTS["Ranger"], ())
        self.assertEqual(SRD_LEVEL_NINE_SPELL_LISTS["Bard"][0].spell_name, "Foresight")
        self.assertEqual(SRD_LEVEL_NINE_SPELL_LISTS["Wizard"][-1].spell_name, "Wish")

    def test_duplicate_or_unowned_record_fails_closed(self):
        first = SRD_CONTENT_CENSUS.records[0]
        with self.assertRaises(ContentCensusError):
            build_content_census((first, first))
        invalid = replace(first, owner_task="P04-UNKNOWN")
        with self.assertRaises(ContentCensusError):
            build_content_census((invalid,))

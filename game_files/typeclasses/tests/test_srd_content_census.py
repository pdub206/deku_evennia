"""P04-A01 immutable source-census coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.srd_content_census import (
    SRD_CONTENT_CENSUS,
    ContentCensusError,
    build_content_census,
    census_report,
)
from systems.srd_spell_lists import (
    SRD_CANTRIP_LISTS,
    SRD_LEVEL_EIGHT_SPELL_LISTS,
    SRD_LEVEL_FIVE_SPELL_LISTS,
    SRD_LEVEL_FOUR_SPELL_LISTS,
    SRD_LEVEL_NINE_SPELL_LISTS,
    SRD_LEVEL_ONE_SPELL_LISTS,
    SRD_LEVEL_SEVEN_SPELL_LISTS,
    SRD_LEVEL_SIX_SPELL_LISTS,
    SRD_LEVEL_THREE_SPELL_LISTS,
    SRD_LEVEL_TWO_SPELL_LISTS,
)


class TestSRDContentCensus(EvenniaTest):
    """Every authored progression row has one accountable P-04 record."""

    def test_census_is_immutable_and_covers_current_source_projection(self):
        report = census_report()

        self.assertEqual(SRD_CONTENT_CENSUS.version, 1)
        self.assertEqual(len(SRD_CONTENT_CENSUS.records), 1936)
        self.assertEqual(len(report["released"]), 3)
        self.assertIn("feature:fighter.second_wind:level:1", report["released"])
        self.assertIn("feature:barbarian.rage:level:1", report["catalogued"])
        self.assertIn("feat:magic_initiate", report["catalogued"])
        self.assertIn("equipment:weapon:longbow", report["catalogued"])
        self.assertIn("equipment:armor:plate_armor", report["catalogued"])
        self.assertIn("equipment:tool:thieves’_tools", report["catalogued"])
        self.assertIn("equipment:adventuring_gear:healer’s_kit", report["catalogued"])
        self.assertIn("creature:mount:warhorse", report["catalogued"])
        self.assertIn("creature:stat_block:aboleth", report["catalogued"])
        self.assertIn("creature:stat_block:tough_boss", report["catalogued"])
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
        self.assertIn(
            "origin_grant:background:acolyte:ability_adjustments",
            report["catalogued"],
        )
        self.assertIn(
            "origin_grant:species:dragonborn:draconic_flight:level:5",
            report["catalogued"],
        )

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
        self.assertEqual(
            sum(len(entries) for entries in SRD_CANTRIP_LISTS.values()), 65
        )
        self.assertEqual(SRD_CANTRIP_LISTS["Paladin"], ())
        self.assertEqual(SRD_CANTRIP_LISTS["Ranger"], ())

    def test_feat_and_epic_boon_inventory_matches_the_pinned_srd(self):
        """P04-A01 has one classified record for every SRD feat-section entry."""
        feat_records = {
            record.key: record
            for record in SRD_CONTENT_CENSUS.records
            if record.kind == "feat"
        }

        self.assertEqual(len(feat_records), 17)
        self.assertEqual(
            {record.adaptation for record in feat_records.values()},
            {"origin feat", "general feat", "fighting style feat", "epic boon feat"},
        )
        self.assertEqual(
            sorted(
                record.key
                for record in feat_records.values()
                if record.adaptation == "origin feat"
            ),
            [
                "feat:alert",
                "feat:magic_initiate",
                "feat:savage_attacker",
                "feat:skilled",
            ],
        )
        self.assertEqual(
            sorted(
                record.key
                for record in feat_records.values()
                if record.adaptation == "epic boon feat"
            ),
            [
                "feat:boon_of_combat_prowess",
                "feat:boon_of_dimensional_travel",
                "feat:boon_of_fate",
                "feat:boon_of_irresistible_offense",
                "feat:boon_of_spell_recall",
                "feat:boon_of_the_night_spirit",
                "feat:boon_of_truesight",
            ],
        )

    def test_background_grant_inventory_preserves_all_srd_chargen_rows(self):
        """P04-A01 gives each pinned-SRD background grant its own occurrence."""
        grant_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("origin_grant:background:")
        ]

        self.assertEqual(len(grant_records), 20)
        self.assertEqual({record.owner_task for record in grant_records}, {"P04-O01"})
        self.assertEqual(
            {record.adapter for record in grant_records},
            {
                "origin.ability_adjustment",
                "origin.skill_proficiencies",
                "origin.tool_proficiency",
                "origin.feat_grant",
                "origin.starting_equipment",
            },
        )
        self.assertIn(
            "origin_grant:background:soldier:equipment",
            {record.key for record in grant_records},
        )
        self.assertEqual(
            {record.key.split(":")[2] for record in grant_records},
            {"acolyte", "criminal", "sage", "soldier"},
        )

    def test_species_grant_inventory_covers_all_pinned_srd_occurrences(self):
        """P04-A01 inventories each species trait, choice, and level gate."""
        species_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("origin_grant:species:")
        ]

        self.assertEqual(len(species_records), 112)
        self.assertEqual({record.owner_task for record in species_records}, {"P04-O01"})
        self.assertEqual(
            {record.key.split(":")[2] for record in species_records},
            {
                "dragonborn",
                "dwarf",
                "elf",
                "gnome",
                "goliath",
                "halfling",
                "human",
                "orc",
                "tiefling",
            },
        )
        self.assertIn(
            "origin_grant:species:dwarf:dwarven_toughness:level:20",
            {record.key for record in species_records},
        )
        self.assertIn(
            "origin_grant:species:tiefling:infernal_darkness:level:5",
            {record.key for record in species_records},
        )

    def test_tool_inventory_covers_all_named_srd_tool_rows_and_variants(self):
        """P04-A01 preserves separate proficiency evidence for tool variants."""
        tool_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("equipment:tool:")
        ]

        self.assertEqual(len(tool_records), 39)
        self.assertEqual({record.owner_task for record in tool_records}, {"P04-A05"})
        self.assertEqual(
            {record.adaptation for record in tool_records},
            {"artisan tool", "other tool", "tool variant"},
        )
        self.assertIn(
            "equipment:tool:three-dragon_ante",
            {record.key for record in tool_records},
        )

    def test_adventuring_gear_inventory_covers_every_pinned_table_row(self):
        """P04-A01 tracks gear rows before P04-A05 supplies mechanics."""
        gear_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("equipment:adventuring_gear:")
        ]

        self.assertEqual(len(gear_records), 82)
        self.assertEqual({record.owner_task for record in gear_records}, {"P04-A05"})
        self.assertIn(
            "equipment:adventuring_gear:spell_scroll_(level_1)",
            {record.key for record in gear_records},
        )

    def test_equipment_variants_and_mount_references_cover_source_tables(self):
        """P04-A01 preserves the table rows needed by P04-A05 and P04-A08."""
        variant_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith(
                (
                    "equipment:ammunition:",
                    "equipment:focus:",
                    "equipment:tack:",
                    "equipment:vehicle:",
                )
            )
        ]
        mount_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("creature:mount:")
        ]

        self.assertEqual(len(variant_records), 33)
        self.assertEqual(len(mount_records), 8)
        self.assertEqual({record.owner_task for record in variant_records}, {"P04-A05"})
        self.assertEqual({record.owner_task for record in mount_records}, {"P04-A08"})
        self.assertIn(
            "equipment:focus:sprig_of_mistletoe",
            {record.key for record in variant_records},
        )

    def test_creature_stat_block_inventory_covers_the_pinned_srd_index(self):
        """P04-A01 records every Monster A-Z and Animals source reference."""
        stat_block_records = [
            record
            for record in SRD_CONTENT_CENSUS.records
            if record.key.startswith("creature:stat_block:")
        ]

        self.assertEqual(len(stat_block_records), 330)
        self.assertEqual(
            {record.owner_task for record in stat_block_records}, {"P04-A08"}
        )
        self.assertEqual(
            {record.adapter for record in stat_block_records},
            {"world.creature_reference"},
        )
        self.assertEqual(
            {record.adaptation for record in stat_block_records},
            {"stat-block reference only; mechanics remain unavailable"},
        )
        self.assertIn(
            "creature:stat_block:adult_red_dragon",
            {record.key for record in stat_block_records},
        )
        self.assertIn(
            "creature:stat_block:tyrannosaurus_rex",
            {record.key for record in stat_block_records},
        )

    def test_level_one_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S01 retains per-class ownership for shared level-one spells."""
        self.assertEqual(tuple(SRD_LEVEL_ONE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_ONE_SPELL_LISTS.values()), 145
        )
        self.assertEqual(
            SRD_LEVEL_ONE_SPELL_LISTS["Bard"][0].spell_name, "Animal Friendship"
        )
        self.assertEqual(
            SRD_LEVEL_ONE_SPELL_LISTS["Wizard"][-1].spell_name, "Unseen Servant"
        )

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
        self.assertEqual(
            SRD_LEVEL_THREE_SPELL_LISTS["Bard"][0].spell_name, "Bestow Curse"
        )
        self.assertEqual(
            SRD_LEVEL_THREE_SPELL_LISTS["Wizard"][-1].spell_name, "Water Breathing"
        )

    def test_level_four_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S04 preserves spell-list membership for every level-four row."""
        self.assertEqual(tuple(SRD_LEVEL_FOUR_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_FOUR_SPELL_LISTS.values()), 90
        )
        self.assertEqual(
            SRD_LEVEL_FOUR_SPELL_LISTS["Bard"][0].spell_name, "Charm Monster"
        )
        self.assertEqual(
            SRD_LEVEL_FOUR_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Fire"
        )

    def test_level_five_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S05 preserves spell-list membership for every level-five row."""
        self.assertEqual(tuple(SRD_LEVEL_FIVE_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_FIVE_SPELL_LISTS.values()), 95
        )
        self.assertEqual(
            SRD_LEVEL_FIVE_SPELL_LISTS["Bard"][0].spell_name, "Animate Objects"
        )
        self.assertEqual(
            SRD_LEVEL_FIVE_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Stone"
        )

    def test_level_six_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S06 preserves spell-list membership for every level-six row."""
        self.assertEqual(tuple(SRD_LEVEL_SIX_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_SIX_SPELL_LISTS.values()), 63
        )
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Ranger"], ())
        self.assertEqual(SRD_LEVEL_SIX_SPELL_LISTS["Bard"][0].spell_name, "Eyebite")
        self.assertEqual(
            SRD_LEVEL_SIX_SPELL_LISTS["Wizard"][-1].spell_name, "Wall of Ice"
        )

    def test_level_seven_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S07 preserves spell-list membership for every level-seven row."""
        self.assertEqual(tuple(SRD_LEVEL_SEVEN_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_SEVEN_SPELL_LISTS.values()), 52
        )
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_SEVEN_SPELL_LISTS["Ranger"], ())
        self.assertEqual(
            SRD_LEVEL_SEVEN_SPELL_LISTS["Bard"][0].spell_name, "Arcane Sword"
        )
        self.assertEqual(
            SRD_LEVEL_SEVEN_SPELL_LISTS["Wizard"][-1].spell_name, "Teleport"
        )

    def test_level_eight_spell_source_rows_cover_all_eight_class_lists(self):
        """P04-S08 preserves spell-list membership for every level-eight row."""
        self.assertEqual(tuple(SRD_LEVEL_EIGHT_SPELL_LISTS), tuple(SRD_CANTRIP_LISTS))
        self.assertEqual(
            sum(len(entries) for entries in SRD_LEVEL_EIGHT_SPELL_LISTS.values()), 42
        )
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Paladin"], ())
        self.assertEqual(SRD_LEVEL_EIGHT_SPELL_LISTS["Ranger"], ())
        self.assertEqual(
            SRD_LEVEL_EIGHT_SPELL_LISTS["Bard"][0].spell_name, "Antipathy/Sympathy"
        )
        self.assertEqual(
            SRD_LEVEL_EIGHT_SPELL_LISTS["Wizard"][-1].spell_name, "Sunburst"
        )

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

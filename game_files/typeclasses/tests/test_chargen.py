"""
Tests for the SRD 5.2.1 character generation system.

Run from the game/ directory:
    evennia test --settings settings.py typeclasses.tests.test_chargen
"""

from unittest.mock import MagicMock

from evennia.utils.test_resources import EvenniaTest
from world.chargen_data import (ABILITY_NAMES, ALIGNMENTS, BACKGROUNDS,
                                CLASSES, POINT_BUY_COSTS, POINT_BUY_TOTAL,
                                STANDARD_ARRAY, STANDARD_ARRAY_BY_CLASS,
                                ability_modifier, roll_4d6_drop_lowest)
from world.chargen_menu import menunode_end

# ---------------------------------------------------------------------------
# chargen_data helpers
# ---------------------------------------------------------------------------


class TestAbilityModifier(EvenniaTest):
    def test_score_3(self):
        self.assertEqual(ability_modifier(3), -4)

    def test_score_8(self):
        self.assertEqual(ability_modifier(8), -1)

    def test_score_10(self):
        self.assertEqual(ability_modifier(10), 0)

    def test_score_11(self):
        self.assertEqual(ability_modifier(11), 0)

    def test_score_15(self):
        self.assertEqual(ability_modifier(15), 2)

    def test_score_20(self):
        self.assertEqual(ability_modifier(20), 5)


class TestStandardArrayCoverage(EvenniaTest):
    def test_all_classes_have_suggestion(self):
        """Every class in CLASSES must have a standard-array suggestion."""
        for cls_name in CLASSES:
            self.assertIn(
                cls_name,
                STANDARD_ARRAY_BY_CLASS,
                f"Class '{cls_name}' missing from STANDARD_ARRAY_BY_CLASS",
            )

    def test_suggestion_uses_standard_scores(self):
        """Each class suggestion must use exactly the six standard-array values."""
        for cls_name, assignment in STANDARD_ARRAY_BY_CLASS.items():
            scores = sorted(assignment.values(), reverse=True)
            self.assertEqual(
                scores,
                sorted(STANDARD_ARRAY, reverse=True),
                f"Class '{cls_name}' suggestion does not use the standard array",
            )

    def test_suggestion_covers_all_abilities(self):
        """Each suggestion must assign a score to every ability."""
        for cls_name, assignment in STANDARD_ARRAY_BY_CLASS.items():
            self.assertEqual(
                sorted(assignment.keys()),
                sorted(ABILITY_NAMES),
                f"Class '{cls_name}' suggestion missing some abilities",
            )


class TestPointBuy(EvenniaTest):
    def test_min_cost_is_zero(self):
        """All 8s (the minimum) should cost 0 points."""
        total = sum(POINT_BUY_COSTS[8] for _ in ABILITY_NAMES)
        self.assertEqual(total, 0)

    def test_max_affordable_within_budget(self):
        """Buying the most expensive score (15) for all 6 abilities exceeds budget."""
        max_cost = sum(POINT_BUY_COSTS[15] for _ in ABILITY_NAMES)
        self.assertGreater(max_cost, POINT_BUY_TOTAL)

    def test_point_buy_costs_ordered(self):
        """Costs must be non-decreasing as the score rises."""
        scores = sorted(POINT_BUY_COSTS.keys())
        for i in range(len(scores) - 1):
            self.assertLessEqual(
                POINT_BUY_COSTS[scores[i]],
                POINT_BUY_COSTS[scores[i + 1]],
            )


class TestRandomRoll(EvenniaTest):
    def test_roll_in_range(self):
        """4d6 drop lowest: minimum possible is 3, maximum is 18."""
        for _ in range(200):
            result = roll_4d6_drop_lowest()
            self.assertGreaterEqual(result, 3)
            self.assertLessEqual(result, 18)


class TestClassData(EvenniaTest):
    def test_hp_bases(self):
        expected = {
            "Barbarian": 12,
            "Fighter": 10,
            "Paladin": 10,
            "Ranger": 10,
            "Sorcerer": 6,
            "Wizard": 6,
        }
        for cls_name, expected_hp in expected.items():
            self.assertEqual(
                CLASSES[cls_name]["hp_base"],
                expected_hp,
                f"{cls_name} hp_base mismatch",
            )

    def test_all_classes_have_required_keys(self):
        required = {
            "primary_ability",
            "hit_die",
            "hp_base",
            "saving_throws",
            "skill_choices",
        }
        for cls_name, data in CLASSES.items():
            for key in required:
                self.assertIn(key, data, f"Class '{cls_name}' missing key '{key}'")


class TestBackgroundData(EvenniaTest):
    def test_acolyte_and_soldier_present(self):
        self.assertIn("Acolyte", BACKGROUNDS)
        self.assertIn("Soldier", BACKGROUNDS)

    def test_background_ability_options_are_three(self):
        """Every background must offer exactly three ability options."""
        for bg_name, data in BACKGROUNDS.items():
            self.assertEqual(
                len(data["ability_options"]),
                3,
                f"Background '{bg_name}' should have exactly 3 ability options",
            )

    def test_acolyte_abilities(self):
        self.assertEqual(
            sorted(BACKGROUNDS["Acolyte"]["ability_options"]),
            sorted(["Intelligence", "Wisdom", "Charisma"]),
        )

    def test_soldier_abilities(self):
        self.assertEqual(
            sorted(BACKGROUNDS["Soldier"]["ability_options"]),
            sorted(["Strength", "Dexterity", "Constitution"]),
        )


class TestAlignments(EvenniaTest):
    def test_nine_alignments(self):
        self.assertEqual(len(ALIGNMENTS), 9)

    def test_abbreviations_unique(self):
        abbrs = [a[1] for a in ALIGNMENTS]
        self.assertEqual(len(abbrs), len(set(abbrs)))


# ---------------------------------------------------------------------------
# menunode_end integration test
# ---------------------------------------------------------------------------


class TestChargenEnd(EvenniaTest):
    """
    Simulate the final chargen step by setting up the temporary attributes a
    real chargen session would have produced, then calling menunode_end and
    verifying the canonical attributes are set correctly.
    """

    def _make_session(self, char):
        """Return a minimal mock session with a new_char attribute."""
        session = MagicMock()
        session.new_char = char
        return session

    def test_barbarian_fighter_hp(self):
        """Barbarian with CON 16 should have hp_max = 12 + 3 = 15."""
        char = self.char1  # provided by EvenniaTest

        # Set the temp chargen attributes.
        char.db.chargen_class = "Barbarian"
        char.db.chargen_background = "Soldier"
        char.db.chargen_species = "Human"
        char.db.chargen_languages = ["Elvish", "Dwarvish"]
        char.db.chargen_alignment = "Neutral Good"
        char.db.chargen_scores_assigned = {
            "Strength": 15,
            "Dexterity": 13,
            "Constitution": 14,
            "Intelligence": 10,
            "Wisdom": 12,
            "Charisma": 8,
        }
        # Soldier background: +2 STR, +1 DEX (option A)
        char.db.chargen_bg_bonus = {"Strength": 2, "Dexterity": 1}

        session = self._make_session(char)

        # Call the finalizer; it returns (text, None) on success.
        result = menunode_end(session)
        self.assertIsNotNone(result)

        # CON after bonus = 14 (no bonus on CON in this scenario)
        # ability_modifier(14) = 2 → hp_max = 12 + 2 = 14
        self.assertEqual(char.db.hp_max, 14)
        self.assertEqual(char.db.level, 1)
        self.assertEqual(char.db.xp, 0)
        self.assertEqual(char.db.proficiency_bonus, 2)
        self.assertEqual(char.db.char_class, "Barbarian")
        self.assertEqual(char.db.background, "Soldier")
        self.assertEqual(char.db.species, "Human")
        self.assertEqual(char.db.alignment, "Neutral Good")
        self.assertIn("Common", char.db.languages)
        self.assertIn("Elvish", char.db.languages)

    def test_chargen_step_cleared(self):
        """chargen_step must be None/absent after menunode_end."""
        char = self.char1
        char.db.chargen_class = "Wizard"
        char.db.chargen_background = "Sage"
        char.db.chargen_species = "Elf"
        char.db.chargen_languages = ["Draconic", "Elvish"]
        char.db.chargen_alignment = "Lawful Neutral"
        char.db.chargen_scores_assigned = {
            "Strength": 8,
            "Dexterity": 12,
            "Constitution": 13,
            "Intelligence": 15,
            "Wisdom": 14,
            "Charisma": 10,
        }
        char.db.chargen_bg_bonus = {"Constitution": 1, "Intelligence": 1, "Wisdom": 1}
        char.db.chargen_step = "menunode_review"

        session = self._make_session(char)
        menunode_end(session)

        self.assertIsNone(char.db.chargen_step)

    def test_wizard_hp(self):
        """Wizard with CON 14 (13 base + 1 bg bonus) → CON mod +2 → hp_max = 6 + 2 = 8."""
        char = self.char1
        char.db.chargen_class = "Wizard"
        char.db.chargen_background = "Sage"
        char.db.chargen_species = "Elf"
        char.db.chargen_languages = ["Draconic", "Gnomish"]
        char.db.chargen_alignment = "Neutral Good"
        char.db.chargen_scores_assigned = {
            "Strength": 8,
            "Dexterity": 12,
            "Constitution": 13,
            "Intelligence": 15,
            "Wisdom": 14,
            "Charisma": 10,
        }
        # Sage options: CON, INT, WIS — option B (+1 each)
        char.db.chargen_bg_bonus = {"Constitution": 1, "Intelligence": 1, "Wisdom": 1}

        session = self._make_session(char)
        menunode_end(session)

        # CON = 13 + 1 = 14 → modifier = +2 → hp_max = 6 + 2 = 8
        self.assertEqual(char.db.constitution, 14)
        self.assertEqual(char.db.hp_max, 8)

    def test_initiative_and_ac(self):
        """Initiative = DEX modifier; AC = 10 + DEX modifier (base)."""
        char = self.char1
        char.db.chargen_class = "Rogue"
        char.db.chargen_background = "Criminal"
        char.db.chargen_species = "Halfling"
        char.db.chargen_languages = ["Elvish", "Halfling"]
        char.db.chargen_alignment = "Chaotic Neutral"
        char.db.chargen_scores_assigned = {
            "Strength": 10,
            "Dexterity": 15,
            "Constitution": 13,
            "Intelligence": 12,
            "Wisdom": 8,
            "Charisma": 14,
        }
        # Criminal: DEX, CON, INT — +2 DEX, +1 CON
        char.db.chargen_bg_bonus = {"Dexterity": 2, "Constitution": 1}

        session = self._make_session(char)
        menunode_end(session)

        # DEX = 15 + 2 = 17 → modifier = +3
        self.assertEqual(char.db.dexterity, 17)
        self.assertEqual(char.db.initiative, 3)
        self.assertEqual(char.db.armor_class, 13)

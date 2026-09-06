"""ADV-04 coverage for the project-owned action-check service."""

from evennia.utils.test_resources import EvenniaTest
from systems.checks import (
    CheckError,
    CheckRequest,
    RollMode,
    active_detection,
    passive_check,
    resolve_check,
    resolve_opposed_check,
    validate_dc,
)


class TestChecks(EvenniaTest):
    """Checks calculate transparent, side-effect-free shared mechanics."""

    def setUp(self):
        super().setUp()
        self.char1.db.strength = 14
        self.char1.db.dexterity = 14
        self.char1.db.wisdom = 14

    def test_skill_proficiency_and_expertise_are_not_duplicate_entries(self):
        self.char1.db.skill_proficiencies = ["Athletics", "Athletics"]
        result = resolve_check(
            CheckRequest(self.char1, "Strength", 10, skill="Athletics"),
            roller=lambda _: 7,
        )
        expertise = resolve_check(
            CheckRequest(
                self.char1, "Strength", 10, skill="Athletics", expertise_multiplier=2
            ),
            roller=lambda _: 4,
        )
        self.assertEqual(
            (result.ability_modifier, result.proficiency_contribution), (2, 2)
        )
        self.assertEqual(expertise.proficiency_contribution, 4)
        self.assertTrue(result.success)

    def test_alternate_ability_sources_and_normal_check_outcomes(self):
        with self.assertRaises(CheckError):
            resolve_check(CheckRequest(self.char1, "Strength", 10, skill="Stealth"))
        self.char1.db.stat_modifiers = {"check_bonus": 1, "skill:stealth": 2}
        result = resolve_check(
            CheckRequest(
                self.char1,
                "Wisdom",
                15,
                skill="Stealth",
                alternate_ability=True,
                advantage_sources=("help", "help"),
                disadvantage_sources=("armor",),
            ),
            roller=lambda _: 12,
        )
        self.assertEqual(result.roll_mode, RollMode.CANCELLED)
        self.assertEqual(result.other_modifiers, 3)
        self.assertEqual(result.total, 17)
        self.assertTrue(result.success)

    def test_passive_and_opposed_ties_preserve_status_quo(self):
        self.char1.db.skill_proficiencies = ["Perception"]
        passive = passive_check(
            self.char1,
            ability="Wisdom",
            skill="Perception",
            disadvantage_sources=("dim",),
        )
        self.assertEqual(passive.roll_mode, RollMode.PASSIVE)
        self.assertEqual(passive.total, 9)
        self.char2.db.strength = 14
        tied = resolve_opposed_check(
            CheckRequest(self.char1, "Strength", 5, action_key="push"),
            CheckRequest(self.char2, "Strength", 5, action_key="resist"),
            roller=lambda _: 10,
        )
        self.assertTrue(tied.tie)
        self.assertFalse(tied.actor_wins)
        detected = active_detection(self.char1, self.char2, roller=lambda _: 10)
        self.assertIsInstance(detected.actor_wins, bool)

    def test_dc_is_bounded_and_checks_do_not_mutate_actor(self):
        with self.assertRaises(CheckError):
            validate_dc(31)
        before = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char1.attributes.all()
        )
        resolve_check(CheckRequest(self.char1, "Strength", 10), roller=lambda _: 20)
        after = tuple(
            (attribute.key, attribute.value)
            for attribute in self.char1.attributes.all()
        )
        self.assertEqual(after, before)

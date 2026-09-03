"""Tests for the canonical PC/NPC statistic interface."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest


class TestCharacterStats(EvenniaTest):
    """Derived values react to persistent inputs and live modifier sources."""

    def setUp(self):
        super().setUp()
        char = self.char1
        for ability in (
            "strength",
            "dexterity",
            "constitution",
            "intelligence",
            "wisdom",
            "charisma",
        ):
            setattr(char.db, ability, 10)
        char.db.char_class = "Fighter"
        char.db.species = "Human"
        char.db.size = "Medium"
        char.db.level = 1
        char.db.xp = 0
        char.db.hp_base = 10
        char.db.hp_current = 10
        char.db.speed = 30
        char.db.skill_proficiencies = []

    def test_core_defaults_and_falsey_values(self):
        stats = self.char1.stats

        self.assertEqual(stats.level, 1)
        self.assertEqual(stats.xp, 0)
        self.assertEqual(stats.hp_current, 10)
        self.assertEqual(stats.hp_max, 10)
        self.assertEqual(stats.armor_class, 10)
        self.assertEqual(stats.reaction_modifier, 0)
        self.assertEqual(stats.speed, 30)
        self.assertEqual(stats.carry_capacity, 150)

        self.char1.db.speed = 0
        self.assertEqual(stats.speed, 0)
        self.assertIsNone(stats.movement_delay(2.0))

    def test_ability_changes_recalculate_related_stats(self):
        stats = self.char1.stats

        stats.set_ability_score("DEX", 14)
        stats.set_ability_score("Constitution", 14)

        self.assertEqual(stats.ability_modifier("Dexterity"), 2)
        self.assertEqual(stats.armor_class, 12)
        self.assertEqual(stats.reaction_modifier, 2)
        self.assertEqual(stats.hp_max, 12)

    def test_proficiency_bonus_tracks_level(self):
        expected = {1: 2, 4: 2, 5: 3, 9: 4, 13: 5, 17: 6, 20: 6}

        for level, proficiency_bonus in expected.items():
            self.char1.stats.set_level(level)
            self.assertEqual(self.char1.stats.proficiency_bonus, proficiency_bonus)

    def test_hp_mutations_are_clamped(self):
        stats = self.char1.stats

        self.assertEqual(stats.take_damage(4), 6)
        self.assertEqual(stats.take_damage(100), 0)
        self.assertEqual(stats.heal(3), 3)
        self.assertEqual(stats.heal(100), 10)

        with self.assertRaises(ValueError):
            stats.take_damage(-1)
        with self.assertRaises(ValueError):
            stats.heal(-1)

    def test_current_hp_is_repaired_when_maximum_falls(self):
        stats = self.char1.stats
        stats.set_ability_score("Constitution", 14)
        stats.set_hp(12)

        stats.set_ability_score("Constitution", 8)

        self.assertEqual(stats.hp_max, 9)
        self.assertEqual(stats.hp_current, 9)
        self.assertEqual(self.char1.db.hp_current, 9)

    def test_skill_save_and_passive_perception_bonuses(self):
        stats = self.char1.stats
        stats.set_ability_score("Strength", 14)
        stats.set_ability_score("Wisdom", 12)
        self.char1.db.skill_proficiencies = ["Athletics", "Perception"]

        self.assertEqual(stats.skill_bonus("Athletics"), 4)
        self.assertEqual(stats.saving_throw_bonus("Strength"), 4)
        self.assertEqual(stats.saving_throw_bonus("Wisdom"), 1)
        self.assertEqual(stats.passive_perception, 13)

    def test_unarmed_attack_profile(self):
        self.char1.stats.set_ability_score("Strength", 14)

        profile = self.char1.stats.attack_profile()

        self.assertEqual(profile.name, "unarmed strike")
        self.assertEqual(profile.attack_bonus, 4)
        self.assertIsNone(profile.damage_dice)
        self.assertEqual(profile.damage_base, 1)
        self.assertEqual(profile.damage_bonus, 2)
        self.assertEqual(profile.damage_type, "bludgeoning")

    def test_equipment_modifiers_appear_and_disappear_immediately(self):
        item = create_object(
            "typeclasses.objects.Item",
            key="a quicksilver vest",
            location=self.char1,
            attributes=(
                ("worn_location", "body"),
                (
                    "stat_modifiers",
                    {
                        "ability:dexterity": 2,
                        "armor_class": 1,
                        "reaction": 1,
                        "speed": 5,
                    },
                ),
            ),
        )

        self.assertEqual(self.char1.stats.armor_class, 12)
        self.assertEqual(self.char1.stats.reaction_modifier, 2)
        self.assertEqual(self.char1.stats.speed, 35)

        item.db.worn_location = None

        self.assertEqual(self.char1.stats.armor_class, 10)
        self.assertEqual(self.char1.stats.reaction_modifier, 0)
        self.assertEqual(self.char1.stats.speed, 30)

    def test_effect_hook_recalculates_without_cached_values(self):
        with patch.object(
            type(self.char1),
            "get_effect_stat_modifier_sources",
            return_value=({"armor_class": 2, "saving_throw": 1},),
        ):
            self.assertEqual(self.char1.stats.armor_class, 12)
            self.assertEqual(self.char1.stats.saving_throw_bonus("Wisdom"), 1)

        self.assertEqual(self.char1.stats.armor_class, 10)
        self.assertEqual(self.char1.stats.saving_throw_bonus("Wisdom"), 0)

    def test_reaction_and_speed_scale_delays(self):
        stats = self.char1.stats
        stats.set_ability_score("Dexterity", 14)

        self.assertAlmostEqual(stats.combat_delay(10.0), 9.6)
        self.assertAlmostEqual(stats.movement_delay(2.0), 2.0)

        self.char1.db.speed = 20
        self.assertAlmostEqual(stats.movement_delay(2.0), 3.0)

        self.char1.db.stat_modifiers = {"reaction": 100}
        self.assertAlmostEqual(stats.combat_delay(10.0), 8.0)

    def test_explicit_npc_overrides_remain_supported(self):
        self.char1.db.proficiency_bonus_override = 7
        self.char1.db.hp_max_override = 40
        self.char1.db.armor_class_override = 16
        self.char1.db.reaction_modifier_override = 3
        self.char1.db.passive_perception_override = 18

        self.assertEqual(self.char1.stats.proficiency_bonus, 7)
        self.assertEqual(self.char1.stats.hp_max, 40)
        self.assertEqual(self.char1.stats.armor_class, 16)
        self.assertEqual(self.char1.stats.reaction_modifier, 3)
        self.assertEqual(self.char1.stats.passive_perception, 18)

    def test_legacy_hp_snapshot_supplies_compatible_base(self):
        self.char1.attributes.remove("hp_base")
        self.char1.db.constitution = 14
        self.char1.db.hp_max = 12

        self.assertEqual(self.char1.stats.hp_base, 10)
        self.assertEqual(self.char1.stats.hp_max, 12)

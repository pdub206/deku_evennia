"""Tests for the canonical PC/NPC statistic interface."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.equipment import HIT_LOCATIONS


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
        self.assertTrue(profile.proficient)

    def test_primary_armor_categories_and_shield_calculate_ac(self):
        stats = self.char1.stats
        stats.set_ability_score("Dexterity", 14)
        armor = create_object(
            "typeclasses.objects.Item",
            key="studded leather",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("base_ac", 12),
                ("wear_locations", ["body"]),
                ("worn_location", "body"),
            ),
        )
        shield = create_object(
            "typeclasses.objects.Item",
            key="a shield",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "shield"),
                ("base_ac", 2),
                ("wear_locations", ["shield"]),
                ("worn_location", "shield"),
            ),
        )

        self.assertEqual(stats.armor_class, 16)

        armor.db.subtype = "medium"
        armor.db.base_ac = 14
        stats.set_ability_score("Dexterity", 18)
        self.assertEqual(stats.armor_class, 18)

        armor.db.subtype = "heavy"
        armor.db.base_ac = 16
        self.assertEqual(stats.armor_class, 18)

        shield.db.worn_location = None
        self.assertEqual(stats.armor_class, 16)

    def test_locational_armor_does_not_change_ac(self):
        self.char1.stats.set_ability_score("Dexterity", 14)
        create_object(
            "typeclasses.objects.Item",
            key="a plate helmet",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "heavy"),
                ("base_ac", 18),
                ("wear_locations", ["head"]),
                ("worn_location", "head"),
                ("stat_modifiers", {"armor_class": 10}),
            ),
        )

        self.assertEqual(self.char1.stats.armor_class, 12)

    def test_wielded_weapon_supplies_trained_attack_profile(self):
        self.char1.stats.set_ability_score("Strength", 14)
        weapon = create_object(
            "typeclasses.objects.Item",
            key="a longsword",
            location=self.char1,
            attributes=(
                ("type", "weapon"),
                ("subtype", "slashing"),
                ("damage", "1d8"),
                ("weapon_category", "martial"),
                ("weapon_kind", "longsword"),
                ("attack_ability", "strength"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )

        profile = self.char1.stats.attack_profile()

        self.assertEqual(profile.name, "a longsword")
        self.assertEqual(profile.attack_bonus, 4)
        self.assertEqual(profile.damage_dice, "1d8")
        self.assertEqual(profile.damage_base, 0)
        self.assertEqual(profile.damage_bonus, 2)
        self.assertEqual(profile.damage_type, "slashing")
        self.assertTrue(profile.proficient)

        weapon.db.worn_location = None
        self.assertEqual(self.char1.stats.attack_profile().name, "unarmed strike")

    def test_untrained_weapon_keeps_ability_but_not_proficiency_bonus(self):
        self.char1.db.char_class = "Wizard"
        self.char1.stats.set_ability_score("Dexterity", 16)
        create_object(
            "typeclasses.objects.Item",
            key="a hand crossbow",
            location=self.char1,
            attributes=(
                ("type", "weapon"),
                ("subtype", "piercing"),
                ("damage", "1d6"),
                ("weapon_category", "martial"),
                ("weapon_kind", "hand_crossbow"),
                ("attack_ability", "dexterity"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )

        profile = self.char1.stats.attack_profile()

        self.assertEqual(profile.attack_bonus, 3)
        self.assertEqual(profile.damage_bonus, 3)
        self.assertFalse(profile.proficient)

    def test_specific_weapon_training_applies_without_category_training(self):
        self.char1.db.char_class = "Rogue"
        self.char1.stats.set_ability_score("Dexterity", 14)
        longsword = create_object(
            "typeclasses.objects.Item",
            key="a longsword",
            location=self.char1,
            attributes=(
                ("type", "weapon"),
                ("subtype", "slashing"),
                ("damage", "1d8"),
                ("weapon_category", "martial"),
                ("weapon_kind", "long_sword"),
                ("attack_ability", "dexterity"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )

        self.assertTrue(self.char1.equipment.is_weapon_proficient(longsword))
        self.assertEqual(self.char1.stats.attack_profile().attack_bonus, 4)

    def test_locational_mitigation_uses_worn_slot_and_physical_defaults(self):
        helmet = create_object(
            "typeclasses.objects.Item",
            key="a reinforced helmet",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "medium"),
                ("mitigation_percent", 10),
                ("mitigation_flat", 2),
                ("wear_locations", ["head"]),
                ("worn_location", "head"),
            ),
        )

        result = self.char1.stats.mitigate_damage(35, "head", "slashing")

        self.assertEqual(result.percentage, 10)
        self.assertEqual(result.flat, 2)
        self.assertEqual(result.prevented, 5)
        self.assertEqual(result.final, 30)
        self.assertEqual(
            self.char1.stats.mitigate_damage(35, "neck", "slashing").final,
            35,
        )
        self.assertEqual(
            self.char1.stats.mitigate_damage(35, "head", "fire").final,
            35,
        )

        helmet.db.mitigation_types = ["fire"]
        self.assertEqual(
            self.char1.stats.mitigate_damage(35, "head", "fire").final,
            30,
        )
        self.assertEqual(
            self.char1.stats.mitigate_damage(35, "head", "slashing").final,
            35,
        )

    def test_bilateral_and_sided_slots_map_to_distinct_targets(self):
        create_object(
            "typeclasses.objects.Item",
            key="arm guards",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("mitigation_flat", 1),
                ("wear_locations", ["arms"]),
                ("worn_location", "arms"),
            ),
        )
        create_object(
            "typeclasses.objects.Item",
            key="a right bracer",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("mitigation_flat", 2),
                ("wear_locations", ["right wrist"]),
                ("worn_location", "right wrist"),
            ),
        )

        self.assertEqual(
            self.char1.stats.mitigate_damage(5, "right arm", "piercing").final,
            4,
        )
        self.assertEqual(
            self.char1.stats.mitigate_damage(5, "left arm", "piercing").final,
            4,
        )
        self.assertEqual(
            self.char1.stats.mitigate_damage(5, "right wrist", "piercing").final,
            3,
        )
        self.assertEqual(
            self.char1.stats.mitigate_damage(5, "left wrist", "piercing").final,
            5,
        )

    def test_non_target_wear_slot_does_not_protect_another_location(self):
        create_object(
            "typeclasses.objects.Item",
            key="an armored cloak",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("mitigation_flat", 5),
                ("wear_locations", ["about"]),
                ("worn_location", "about"),
            ),
        )

        self.assertEqual(
            self.char1.stats.mitigate_damage(10, "body", "slashing").final,
            10,
        )

    def test_mitigation_caps_percentage_and_can_reduce_damage_to_zero(self):
        create_object(
            "typeclasses.objects.Item",
            key="an overbuilt helmet",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "heavy"),
                ("mitigation_percent", 100),
                ("mitigation_flat", 3),
                ("wear_locations", ["head"]),
                ("worn_location", "head"),
            ),
        )

        result = self.char1.stats.mitigate_damage(10, "head", "bludgeoning")

        self.assertEqual(result.percentage, 80)
        self.assertEqual(result.final, 0)

    def test_mitigation_rejects_invalid_inputs(self):
        self.assertEqual(
            set(HIT_LOCATIONS),
            {
                "head",
                "neck",
                "body",
                "right shoulder",
                "left shoulder",
                "right arm",
                "left arm",
                "right wrist",
                "left wrist",
                "right hand",
                "left hand",
                "right leg",
                "left leg",
                "right foot",
                "left foot",
            },
        )
        with self.assertRaises(ValueError):
            self.char1.stats.mitigate_damage(-1, "head", "slashing")
        with self.assertRaises(ValueError):
            self.char1.stats.mitigate_damage(1, "ankle", "slashing")
        with self.assertRaises(ValueError):
            self.char1.stats.mitigate_damage(1, "head", "imaginary")

    def test_untrained_armor_is_allowed_and_exposed_to_later_rules(self):
        self.char1.db.char_class = "Wizard"
        armor = create_object(
            "typeclasses.objects.Item",
            key="plate armor",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "heavy"),
                ("base_ac", 18),
                ("wear_locations", ["body"]),
                ("worn_location", "body"),
            ),
        )

        self.assertFalse(self.char1.equipment.is_armor_proficient(armor))
        self.assertTrue(self.char1.stats.has_untrained_armor)
        self.assertEqual(self.char1.stats.armor_class, 18)

        armor.db.worn_location = None
        self.assertFalse(self.char1.stats.has_untrained_armor)

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

        self.assertEqual(self.char1.stats.armor_class, 11)
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

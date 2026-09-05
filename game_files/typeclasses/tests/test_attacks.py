"""Deterministic COMBAT-02 tests for basic attack resolution."""

from unittest.mock import patch

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.attacks import AttackOutcome, can_attack, resolve_basic_attack
from systems.combat import (
    COMBAT_CONFIG_KEY,
    process_combat_pulse,
    set_combat_action_hook,
    start_fight,
)
from systems.dice import roll_damage_expression
from systems.pulses import PulseEvent, PulseLane


class TestDamageExpressions(EvenniaTest):
    """Damage notation stays bounded, non-evaluating, and inspectable."""

    def test_rolls_individual_dice_and_critical_repeats_dice_only(self):
        values = iter((2, 5, 3, 4))
        result = roll_damage_expression(
            "2d6+1", multiplier=2, roller=lambda _: next(values)
        )

        self.assertEqual(result.rolls, (2, 5, 3, 4))
        self.assertEqual(result.modifier, 1)
        self.assertEqual(result.total, 15)

    def test_invalid_or_excessive_expressions_fail(self):
        for expression in ("0d6", "1d0", "1d6;bad", "21d6", "1d1001", "1d6+1001"):
            with self.assertRaises(ValueError):
                roll_damage_expression(expression)


class TestBasicAttacks(EvenniaTest):
    """The resolver follows SRD outcomes and mutates HP exactly once."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False
        self.char1.db.hp_current = 20
        self.char2.db.hp_current = 20

    def test_natural_one_misses_without_mutating_hp(self):
        before = self.char2.stats.hp_current
        result = resolve_basic_attack(
            self.char1,
            self.char2,
            die_roller=lambda _: 1,
            emit_messages=False,
        )

        self.assertEqual(result.outcome, AttackOutcome.MISS)
        self.assertEqual(result.final_damage, 0)
        self.assertEqual(self.char2.stats.hp_current, before)

    def test_natural_twenty_critical_rolls_weapon_dice_twice(self):
        weapon = create_object(
            "typeclasses.objects.Item",
            key="test blade",
            location=self.char1,
            attributes=(
                ("type", "weapon"),
                ("subtype", "slashing"),
                ("damage", "1d6"),
                ("weapon_category", "simple"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
            ),
        )
        self.assertIs(self.char1.equipment.wielded_weapon, weapon)
        self.char1.db.strength = 14
        values = iter((20, 3, 4))
        result = resolve_basic_attack(
            self.char1,
            self.char2,
            die_roller=lambda _: next(values),
            location_selector=lambda *_: "head",
            emit_messages=False,
        )

        self.assertEqual(result.outcome, AttackOutcome.CRITICAL)
        self.assertEqual(result.damage_rolls, (3, 4))
        self.assertEqual(result.damage_total, 9)  # dice plus Strength once

    def test_mitigation_and_negative_damage_do_not_heal(self):
        self.char1.stats.set_ability_score("Strength", 3)
        before = self.char2.stats.hp_current
        result = resolve_basic_attack(
            self.char1,
            self.char2,
            die_roller=lambda _: 20,
            location_selector=lambda *_: "body",
            emit_messages=False,
        )
        self.assertEqual(result.damage_total, 0)
        self.assertEqual(result.final_damage, 0)
        self.assertEqual(self.char2.stats.hp_current, before)

    def test_pvp_is_denied_until_an_explicit_attack_lock_allows_it(self):
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        with patch("systems.attacks._is_staff_override", return_value=False):
            self.assertFalse(can_attack(self.char1, self.char2).allowed)
            self.char2.locks.add("attack:all()")
            self.assertTrue(can_attack(self.char1, self.char2).allowed)

    def test_zero_hp_removes_target_from_scheduled_encounter(self):
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.char2.db.hp_current = 1
        self.char1.stats.set_ability_score("Strength", 18)
        start_fight(self.char1, self.char2)
        set_combat_action_hook(
            lambda actor, target, event: resolve_basic_attack(
                actor,
                target,
                event,
                die_roller=lambda _: 20,
                location_selector=lambda *_: "body",
                emit_messages=False,
            )
        )
        self.addCleanup(set_combat_action_hook, None)

        result = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))

        self.assertEqual(result.actions, 1)
        self.assertLessEqual(self.char2.stats.hp_current, 0)

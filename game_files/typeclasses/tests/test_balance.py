"""COMBAT-10 deterministic offline balance-harness regression coverage."""

import random

from evennia.utils.test_resources import EvenniaTest
from systems.balance import (BalanceScenario, BalanceValidationError,
                             CombatantScenario, render_results, run_scenario,
                             standard_matrix)
from systems.character_stats import AttackProfile
from systems.combat_math import critical_probability, hit_probability
from systems.combat_outcomes import calculate_npc_xp


def _fighter(name: str, **overrides):
    """Create a small immutable test combatant from valid primitive inputs."""
    values = {
        "name": name,
        "level": 3,
        "hp": 20,
        "armor_class": 13,
        "profile": AttackProfile("blade", "Strength", 5, "1d8", 0, 2, "slashing", True),
    }
    values.update(overrides)
    return CombatantScenario(**values)


class TestBalanceHarness(EvenniaTest):
    """The harness is reproducible, bounded, and independent of live state."""

    def test_same_seed_repeats_and_global_random_is_untouched(self):
        scenario = BalanceScenario("repeatable", ((_fighter("a"),), (_fighter("b"),)))
        random.seed(991)
        before = random.getstate()
        first = run_scenario(scenario, iterations=30, seed=42)
        after = random.getstate()

        self.assertEqual(first, run_scenario(scenario, iterations=30, seed=42))
        self.assertEqual(before, after)

    def test_different_seeds_change_observed_rolls_without_changing_rules(self):
        scenario = BalanceScenario("seeded", ((_fighter("a"),), (_fighter("b"),)))

        first = run_scenario(scenario, iterations=20, seed=1)
        second = run_scenario(scenario, iterations=20, seed=2)

        self.assertNotEqual(
            (first.hit_rate, first.critical_rate, first.damage_per_action),
            (second.hit_rate, second.critical_rate, second.damage_per_action),
        )

    def test_limits_and_invalid_inputs_are_explicit(self):
        harmless = AttackProfile("tap", "Strength", 0, None, 0, 0, "bludgeoning", True)
        scenario = BalanceScenario(
            "stalled",
            ((_fighter("a", profile=harmless),), (_fighter("b"),)),
            max_rounds=2,
        )
        result = run_scenario(scenario, iterations=1, seed=1)

        self.assertEqual(result.stalls, 1)
        self.assertEqual(result.diagnostics, ("round_limit",))
        with self.assertRaises(BalanceValidationError):
            run_scenario(scenario, iterations=0, seed=1)
        with self.assertRaises(BalanceValidationError):
            run_scenario(scenario, iterations=1, seed=True)
        with self.assertRaises(BalanceValidationError):
            CombatantScenario("bad", 0, 1, 10, harmless)

    def test_analytical_d20_and_reward_calculations_match_live_rules(self):
        self.assertEqual(hit_probability(5, 13), 0.65)
        self.assertEqual(critical_probability(5, 13), 0.05)
        self.assertEqual(critical_probability(5, 13, unconscious=True), 0.95)
        for delta in range(-9, 11):
            multiplier, xp = calculate_npc_xp(100, 10 + delta, 10)
            self.assertEqual(multiplier, max(0.0, min(2.0, 1.0 + delta * 0.1)))
            self.assertEqual(xp, int(100 * multiplier))

    def test_standard_matrix_and_machine_output_are_stable(self):
        matrix = standard_matrix()
        identities = {scenario.identity for scenario in matrix}
        self.assertIn("two_pcs_vs_npc", identities)
        self.assertIn("reaction_cadence", identities)
        result = run_scenario(matrix[0], iterations=2, seed=3)

        self.assertEqual(
            render_results((result,), "json"), render_results((result,), "json")
        )
        self.assertTrue(render_results((result,), "csv").startswith("scenario,"))
        self.assertIn("Scenario", render_results((result,), "summary"))
        self.assertIn(" | ", render_results((result,), "summary"))

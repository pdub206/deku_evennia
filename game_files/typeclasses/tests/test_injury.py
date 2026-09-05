"""COMBAT-04 injury-state regression coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.action_policy import Position
from systems.injury import (
    InjuryState,
    apply_damage,
    apply_healing,
    attempt_stabilization,
    injury_record,
    process_recovery_pulse,
)
from systems.pulses import PulseEvent, PulseLane


class TestInjuryState(EvenniaTest):
    """HP transitions, saves, and stabilization use one persistent service."""

    def setUp(self):
        super().setUp()
        self.char1.db.hp_current = 10
        self.char2.db.hp_current = 10

    def test_pc_zero_hp_dies_only_after_failed_saves_and_healing_wakes_resting(self):
        result = apply_damage(self.char2, 10, emit_messages=False)
        self.assertEqual(result.state, InjuryState.DYING)
        self.assertEqual(self.char2.action_position, Position.DYING)

        first = process_recovery_pulse(
            PulseEvent(60, PulseLane.RECOVERY, 1), die_roller=lambda _: 2
        )
        self.assertEqual(first.saves, 1)
        self.assertEqual(injury_record(self.char2).failures, 1)
        # Replaying the same durable lane token cannot roll again.
        self.assertEqual(
            process_recovery_pulse(
                PulseEvent(60, PulseLane.RECOVERY, 1), die_roller=lambda _: 1
            ).saves,
            0,
        )
        process_recovery_pulse(
            PulseEvent(120, PulseLane.RECOVERY, 2), die_roller=lambda _: 1
        )
        self.assertEqual(injury_record(self.char2).state, InjuryState.DEAD)
        self.assertFalse(apply_healing(self.char2, 5).accepted)

    def test_natural_twenty_recovers_and_stabilization_uses_medicine_bonus(self):
        apply_damage(self.char2, 10, emit_messages=False)
        process_recovery_pulse(
            PulseEvent(60, PulseLane.RECOVERY, 1), die_roller=lambda _: 20
        )
        self.assertEqual(self.char2.stats.hp_current, 1)
        self.assertEqual(injury_record(self.char2).state, InjuryState.CONSCIOUS)
        self.assertEqual(self.char2.db.position, Position.RESTING.value)

        self.char2.db.hp_current = 10
        self.char2.attributes.remove("injury_state")
        apply_damage(self.char2, 10, emit_messages=False)
        self.char1.db.wisdom = 14
        self.char1.db.skill_proficiencies = ["Medicine"]
        result = attempt_stabilization(
            self.char1, self.char2, die_roller=lambda _: 6, emit_messages=False
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.state, InjuryState.INCAPACITATED)

    def test_npc_default_is_immediately_dead_and_massive_damage_bypasses_saves(self):
        self.char2.db.is_player_character = False
        npc = apply_damage(self.char2, 10, emit_messages=False)
        self.assertEqual(npc.state, InjuryState.DEAD)

        self.char2.db.is_player_character = True
        self.char2.db.hp_current = 10
        self.char2.attributes.remove("injury_state")
        massive = apply_damage(self.char2, 20, emit_messages=False)
        self.assertEqual(massive.reason, "massive_damage")
        self.assertEqual(massive.state, InjuryState.DEAD)

    def test_damage_to_stable_target_adds_failures(self):
        apply_damage(self.char2, 10, emit_messages=False)
        attempt_stabilization(
            self.char1, self.char2, die_roller=lambda _: 20, emit_messages=False
        )
        result = apply_damage(self.char2, 1, critical=True, emit_messages=False)
        self.assertEqual(result.state, InjuryState.DYING)
        self.assertEqual(result.failures, 2)

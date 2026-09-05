"""Tests for RULES-04 pulse-driven resource recovery."""

from unittest.mock import patch

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.action_policy import Position
from systems.effects import EFFECT_REGISTRY, EffectDefinition
from systems.pulses import PulseEvent, PulseLane
from systems.resources import (HP_RESOURCE, RECOVERY_TOKENS_ATTRIBUTE,
                               RecoverySkipReason, process_recovery_pulse,
                               recover_resource)
from typeclasses.characters import Character

_RECOVERY_BOOST = EffectDefinition(
    key="test.rules04.recovery_boost",
    name="Recovery Boost",
    modifiers={"recovery:hp": 2},
)
_RECOVERY_DRAIN = EffectDefinition(
    key="test.rules04.recovery_drain",
    name="Recovery Drain",
    modifiers={"recovery:hp": -100},
)
for _definition in (_RECOVERY_BOOST, _RECOVERY_DRAIN):
    if EFFECT_REGISTRY.get(_definition.key) is None:
        EFFECT_REGISTRY.register(_definition)


class TestEnergyResource:
    """A synthetic persistent resource proving the extension contract."""

    key = "test_energy"

    def current_value(self, owner):
        """Read the resource's current persistent value."""
        return owner.db.test_energy_current or 0

    def maximum_value(self, owner):
        """Read the resource's maximum persistent value."""
        return owner.db.test_energy_max or 10

    def apply_gain(self, owner, amount):
        """Persist a clamped resource gain."""
        owner.db.test_energy_current = min(
            self.maximum_value(owner), self.current_value(owner) + amount
        )
        return owner.db.test_energy_current

    def set_current_value(self, owner, value):
        """Restore a valid resource value after a failed extension hook."""
        owner.db.test_energy_current = min(self.maximum_value(owner), value)
        return owner.db.test_energy_current

    def recovery_gain(self, owner, posture):
        """Recover a fixed amount independent of HP's formula."""
        return 2


class BrokenResource(TestEnergyResource):
    """A test resource whose failed persistence must roll back its mutation."""

    key = "broken_energy"

    def apply_gain(self, owner, amount):
        """Write tentatively, then simulate a persistence failure."""
        self.set_current_value(owner, self.current_value(owner) + amount)
        raise RuntimeError("resource persistence failed")


class TestResourceRecovery(EvenniaTest):
    """HP recovery uses posture, effects, and durable sequence tokens."""

    def setUp(self):
        super().setUp()
        for char in (self.char1, self.char2):
            char.db.level = 1
            char.db.constitution = 14
            char.db.hp_base = 10
            char.db.hp_current = 1
            char.db.position = "standing"

    @staticmethod
    def _event(sequence=1):
        return PulseEvent(sequence * 60, PulseLane.RECOVERY, sequence)

    def test_posture_multipliers_floor_deterministically(self):
        expected = {"standing": 3, "sitting": 3, "resting": 4, "sleeping": 6}
        for sequence, (posture, gain) in enumerate(expected.items(), 1):
            self.char1.db.position = posture
            self.char1.stats.set_hp(1)
            result = recover_resource(self.char1, HP_RESOURCE, self._event(sequence))
            self.assertEqual(result.attempted_gain, gain)
            self.assertEqual(result.final_value, 1 + gain)

    def test_negative_constitution_never_reduces_minimum_recovery(self):
        self.char1.db.constitution = 1
        self.char1.db.level = 1
        result = recover_resource(self.char1, HP_RESOURCE, self._event())
        self.assertEqual(result.attempted_gain, 1)
        self.assertEqual(result.final_value, 2)

    def test_effect_sources_bonus_penalty_and_suppression(self):
        self.char1.effects.add(_RECOVERY_BOOST.key, quiet=True)
        boosted = recover_resource(self.char1, HP_RESOURCE, self._event())
        self.assertEqual(boosted.attempted_gain, 5)

        self.char1.effects.remove_matching("test", quiet=True)
        self.char1.stats.set_hp(1)
        self.char1.effects.add(_RECOVERY_DRAIN.key, quiet=True)
        suppressed = recover_resource(self.char1, HP_RESOURCE, self._event(2))
        self.assertEqual(suppressed.skip_reason, RecoverySkipReason.SUPPRESSED)
        self.assertEqual(suppressed.final_value, 1)

    def test_terminal_states_fighting_zero_and_full_are_consumed_skips(self):
        positions = {
            Position.FIGHTING: RecoverySkipReason.FIGHTING,
            Position.INCAPACITATED: RecoverySkipReason.INCAPACITATED,
            Position.DYING: RecoverySkipReason.DYING,
            Position.DEAD: RecoverySkipReason.DEAD,
        }
        for sequence, (position, reason) in enumerate(positions.items(), 1):
            with patch.object(
                Character, "get_imposed_action_positions", return_value=(position,)
            ):
                result = recover_resource(
                    self.char1, HP_RESOURCE, self._event(sequence)
                )
            self.assertEqual(result.skip_reason, reason)

        self.char1.stats.set_hp(0)
        zero = recover_resource(self.char1, HP_RESOURCE, self._event(5))
        self.assertEqual(zero.skip_reason, RecoverySkipReason.ZERO)
        self.char1.stats.set_hp(self.char1.stats.hp_max)
        full = recover_resource(self.char1, HP_RESOURCE, self._event(6))
        self.assertEqual(full.skip_reason, RecoverySkipReason.FULL)

    def test_duplicate_tokens_do_not_heal_after_damage_even_when_first_was_full(self):
        first = recover_resource(self.char1, HP_RESOURCE, self._event())
        self.char1.stats.take_damage(2)
        duplicate = recover_resource(self.char1, HP_RESOURCE, self._event())
        self.assertEqual(first.final_value, 4)
        self.assertEqual(duplicate.skip_reason, RecoverySkipReason.DUPLICATE)
        self.assertEqual(duplicate.final_value, 2)

        self.char1.stats.set_hp(self.char1.stats.hp_max)
        full = recover_resource(self.char1, HP_RESOURCE, self._event(2))
        self.char1.stats.take_damage(2)
        duplicate_full = recover_resource(self.char1, HP_RESOURCE, self._event(2))
        self.assertEqual(full.skip_reason, RecoverySkipReason.FULL)
        self.assertEqual(duplicate_full.skip_reason, RecoverySkipReason.DUPLICATE)
        self.assertEqual(duplicate_full.final_value, self.char1.stats.hp_max - 2)

    def test_maximum_recalculates_between_pulses_and_tokens_survive_reconstruction(
        self,
    ):
        self.char1.stats.set_hp(11)
        first = recover_resource(self.char1, HP_RESOURCE, self._event())
        self.char1.db.hp_max_override = 15
        second = recover_resource(self.char1, HP_RESOURCE, self._event(2))
        reconstructed = Character.objects.get(pk=self.char1.pk)

        self.assertEqual(first.final_value, 12)
        self.assertEqual(second.final_value, 15)
        self.assertEqual(reconstructed.stats.hp_current, 15)
        self.assertEqual(
            reconstructed.attributes.get(RECOVERY_TOKENS_ATTRIBUTE)["tokens"]["hp"], 2
        )

    def test_synthetic_resource_uses_the_same_persistent_token_path(self):
        resource = TestEnergyResource()
        self.char1.db.test_energy_current = 1
        self.char1.db.test_energy_max = 5

        first = recover_resource(self.char1, resource, self._event())
        self.char1.db.test_energy_current = 0
        duplicate = recover_resource(self.char1, resource, self._event())

        self.assertEqual(
            (first.previous_value, first.attempted_gain, first.final_value), (1, 2, 3)
        )
        self.assertEqual(duplicate.skip_reason, RecoverySkipReason.DUPLICATE)
        self.assertEqual(duplicate.final_value, 0)

    def test_failed_resource_hook_rolls_back_its_partial_mutation(self):
        self.char1.db.test_energy_current = 1
        self.char1.db.test_energy_max = 5

        with self.assertRaisesRegex(RuntimeError, "persistence failed"):
            recover_resource(self.char1, BrokenResource(), self._event())

        self.assertEqual(self.char1.db.test_energy_current, 1)
        self.assertIsNone(self.char1.attributes.get(RECOVERY_TOKENS_ATTRIBUTE))


class TestRecoveryPulse(EvenniaTest):
    """The global recovery lane processes only on-grid PCs and NPCs."""

    def setUp(self):
        super().setUp()
        for char in (self.char1, self.char2):
            char.db.hp_base = 10
            char.db.hp_current = 1
            char.db.constitution = 10
            char.db.position = "standing"

    def test_on_grid_pc_and_npc_recover_while_offline_character_does_not(self):
        npc = create_object(
            "typeclasses.characters.Character", key="Recovery NPC", location=self.room1
        )
        npc.db.hp_base = 10
        npc.db.hp_current = 1
        npc.db.constitution = 10
        self.char2.move_to(None, to_none=True)

        result = process_recovery_pulse(PulseEvent(60, PulseLane.RECOVERY, 1))

        self.assertEqual(
            (result.processed, result.recovered, result.failures), (2, 2, 0)
        )
        self.assertEqual(self.char1.stats.hp_current, 2)
        self.assertEqual(npc.stats.hp_current, 2)
        self.assertEqual(self.char2.stats.hp_current, 1)

    def test_malformed_owner_isolated(self):
        self.char2.db.resource_recovery_tokens = {"version": 999, "tokens": {}}
        event = PulseEvent(60, PulseLane.RECOVERY, 1)
        with patch("systems.resources.logger.log_trace") as log_trace:
            result = process_recovery_pulse(event)
        self.assertEqual(
            (result.processed, result.recovered, result.failures), (1, 1, 1)
        )
        self.assertIn(f"object #{self.char2.id}", log_trace.call_args.args[0])

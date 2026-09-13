"""MAGIC-03 durable Short and Long Rest recovery coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.magic_actions import MAGIC_PREPARATION_ATTRIBUTE
from systems.magic_resources import resource_current, spend_resource
from systems.magic_rest import (
    LONG_REST_PULSES,
    LONG_REST_SLEEP_PULSES,
    MAGIC_REST_ATTRIBUTE,
    MAGIC_REST_VERSION,
    SAFE_REST_TAG,
    SAFE_REST_TAG_CATEGORY,
    SHORT_REST_PULSES,
    advance_magic_rest,
    interrupt_magic_rest,
)
from systems.pulses import PulseEvent, PulseLane


class TestMagicRest(EvenniaTest):
    """Rest progress is durable, posture-bound, and never catches up."""

    def setUp(self):
        super().setUp()
        self.char1.db.char_class = "Fighter"
        self.char1.db.level = 3
        self.char1.db.hp_max_override = 10
        self.char1.db.hp_current = 10
        self.char1.db.position = "resting"
        self.room1.tags.add(SAFE_REST_TAG, category=SAFE_REST_TAG_CATEGORY)

    @staticmethod
    def _event(sequence: int) -> PulseEvent:
        """Return one recovery token at the standard one-minute cadence."""
        return PulseEvent(sequence * 60, PulseLane.RECOVERY, sequence)

    def test_short_rest_restores_only_after_uninterrupted_hour(self):
        """A short-rest class resource restores on the 60th eligible pulse."""
        spend_resource(self.char1, "fighter.second_wind", 1)
        for sequence in range(1, SHORT_REST_PULSES):
            self.assertFalse(
                advance_magic_rest(self.char1, self._event(sequence)).profiles
            )
        completed = advance_magic_rest(self.char1, self._event(SHORT_REST_PULSES))

        self.assertEqual(completed.profiles, ("short_rest",))
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 2)

    def test_interruption_and_sequence_gap_reset_progress_without_recovery(self):
        """Standing or offline time cannot be converted into retroactive rest."""
        spend_resource(self.char1, "fighter.second_wind", 1)
        advance_magic_rest(self.char1, self._event(1))
        self.char1.db.position = "standing"
        stopped = advance_magic_rest(self.char1, self._event(2))
        self.assertEqual(stopped.reason, "interrupted")
        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 1)

        self.char1.db.position = "resting"
        gap = advance_magic_rest(self.char1, self._event(4))
        self.assertEqual(gap.reason, "sequence_gap")
        self.assertEqual(
            self.char1.attributes.get(MAGIC_REST_ATTRIBUTE)["continuous_pulses"], 0
        )

    def test_long_rest_requires_six_hours_asleep_and_restores_slots(self):
        """Long-rest recovery is gated by total and sleeping pulse counts."""
        self.char1.db.char_class = "Wizard"
        self.char1.db.level = 3
        self.char1.db.position = "sleeping"
        spend_resource(self.char1, "wizard.spell_slot.1", 3)
        self.char1.attributes.add(
            MAGIC_REST_ATTRIBUTE,
            {
                "version": MAGIC_REST_VERSION,
                "last_sequence": LONG_REST_PULSES - 1,
                "continuous_pulses": LONG_REST_PULSES - 1,
                "sleep_pulses": LONG_REST_SLEEP_PULSES - 1,
            },
        )
        completed = advance_magic_rest(self.char1, self._event(LONG_REST_PULSES))

        self.assertEqual(completed.profiles, ("short_rest", "long_rest"))
        self.assertEqual(
            self.char1.attributes.get(MAGIC_PREPARATION_ATTRIBUTE),
            {"version": 1, "recovery_sequence": LONG_REST_PULSES},
        )
        self.assertEqual(resource_current(self.char1, "wizard.spell_slot.1"), 4)

    def test_damage_interruption_clears_existing_progress_immediately(self):
        """Damage adapters can reset a rest without waiting for another pulse."""
        advance_magic_rest(self.char1, self._event(1))
        interrupt_magic_rest(self.char1)

        record = self.char1.attributes.get(MAGIC_REST_ATTRIBUTE)
        self.assertEqual(record["last_sequence"], 1)
        self.assertEqual((record["continuous_pulses"], record["sleep_pulses"]), (0, 0))

    def test_forced_movement_interrupts_existing_rest_progress(self):
        """Movement hooks discard rest credit before the next recovery event."""
        advance_magic_rest(self.char1, self._event(1))
        self.char1.move_to(self.room2, quiet=True, move_type="teleport")

        record = self.char1.attributes.get(MAGIC_REST_ATTRIBUTE)
        self.assertEqual(record["continuous_pulses"], 0)

    def test_untagged_room_cannot_complete_a_class_resource_rest(self):
        """Only builders' explicit safe-rest locations permit slot recovery."""
        self.room1.tags.remove(SAFE_REST_TAG, category=SAFE_REST_TAG_CATEGORY)
        result = advance_magic_rest(self.char1, self._event(1))

        self.assertEqual(result.reason, "interrupted")

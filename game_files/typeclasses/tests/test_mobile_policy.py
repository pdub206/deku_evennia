"""MOB-03 regression coverage for NPC policy, combat, and mobile decisions."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.attacks import can_attack
from systems.combat import is_fighting, start_fight
from systems.mobile_policy import (MOBILE_POLICY_ATTRIBUTE, can_detect,
                                   default_mobile_policy, mobile_policy,
                                   set_mobile_policy, validate_mobile_policy)
from systems.mobiles import process_mobile_pulse
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character
from typeclasses.objects import Item


def _event(sequence: int) -> PulseEvent:
    """Build one valid, deterministic mobiles-lane event."""
    return PulseEvent(sequence, PulseLane.MOBILES, sequence)


class TestMobilePolicy(EvenniaTest):
    """The policy profile is builder-safe and governs all entry points."""

    def setUp(self):
        super().setUp()
        self.npc = create_object(Character, key="Guard", location=self.room1)
        self.npc.db.is_player_character = False
        self.target = create_object(Character, key="Target", location=self.room1)
        self.target.db.is_player_character = True

    def test_profile_is_exact_primitive_data_and_malformed_npc_fails_closed(self):
        self.assertEqual(mobile_policy(self.npc), default_mobile_policy())
        with self.assertRaises(ValueError):
            validate_mobile_policy({**default_mobile_policy(), "unknown": True})
        self.npc.attributes.add(MOBILE_POLICY_ATTRIBUTE, {"version": 99})
        self.assertFalse(can_attack(self.npc, self.target).allowed)
        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))
        self.assertEqual(result.outcomes[0].reason, "malformed_mobile_policy")

    def test_protected_and_noncombatant_apply_to_commands_and_encounter_entry(self):
        set_mobile_policy(self.npc, {**default_mobile_policy(), "protected": True})
        self.assertEqual(can_attack(self.target, self.npc).reason, "protected")
        self.assertFalse(start_fight(self.target, self.npc).accepted)

        set_mobile_policy(self.npc, {**default_mobile_policy(), "noncombatant": True})
        self.assertEqual(can_attack(self.npc, self.target).reason, "noncombatant")
        self.assertFalse(start_fight(self.npc, self.target).accepted)

    def test_aggression_is_single_deterministic_mobile_action(self):
        set_mobile_policy(self.npc, {**default_mobile_policy(), "aggressive": True})
        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))
        self.assertEqual(
            (result.outcomes[0].status, result.outcomes[0].reason),
            ("acted", "aggression"),
        )
        self.assertTrue(is_fighting(self.npc))
        duplicate = process_mobile_pulse(_event(1), mobiles=(self.npc,))
        self.assertEqual(duplicate.outcomes[0].reason, "fighting")

    def test_aggression_ignores_targets_already_fighting(self):
        self.char1.location = self.room2
        self.char2.location = self.room2
        opponent = create_object(Character, key="Opponent", location=self.room1)
        start_fight(self.target, opponent)
        set_mobile_policy(self.npc, {**default_mobile_policy(), "aggressive": True})

        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))

        self.assertEqual(result.outcomes[0].reason, "idle")
        self.assertFalse(is_fighting(self.npc))

    def test_detection_and_scavenging_fail_closed_and_take_one_loose_item(self):
        self.target.db.detection_difficulty = 99
        self.assertEqual(can_detect(self.npc, self.target).reason, "detection_denied")
        first = create_object(Item, key="First", location=self.room1)
        second = create_object(Item, key="Second", location=self.room1)
        set_mobile_policy(self.npc, {**default_mobile_policy(), "scavenger": True})
        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))
        self.assertEqual(result.outcomes[0].reason, "scavenge")
        self.assertEqual(first.location, self.npc)
        self.assertEqual(second.location, self.room1)

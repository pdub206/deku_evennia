"""Regression coverage for MOB-06's data-only special-behavior dispatch."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.mobile_specials import (MOBILE_SPECIALS_ATTRIBUTE,
                                     MobileSpecialDefinition,
                                     MobileSpecialError, SpecialEvent,
                                     SpecialOutcome, dispatch_specials,
                                     register_special, set_mobile_specials,
                                     validate_mobile_specials)
from systems.mobiles import process_mobile_pulse
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character


def _mark_first(npc, event, config):
    """Record one test-only special effect."""
    npc.ndb.special_marks = (getattr(npc.ndb, "special_marks", []) or []) + ["first"]
    return SpecialOutcome("acted", "marked")


def _mark_second(npc, event, config):
    """A second action that must be blocked by the event budget."""
    npc.ndb.special_marks = (getattr(npc.ndb, "special_marks", []) or []) + ["second"]
    return SpecialOutcome("acted", "marked")


def _decline(npc, event, config):
    """A deterministic non-action behavior used to exercise ordering."""
    npc.ndb.special_marks = (getattr(npc.ndb, "special_marks", []) or []) + ["decline"]
    return SpecialOutcome("declined", "nothing_to_do")


for definition in (
    MobileSpecialDefinition(
        "test.special.first", frozenset({"decision"}), 20, _mark_first
    ),
    MobileSpecialDefinition(
        "test.special.second", frozenset({"decision"}), 30, _mark_second
    ),
    MobileSpecialDefinition(
        "test.special.decline", frozenset({"decision"}), 10, _decline
    ),
):
    try:
        register_special(definition)
    except MobileSpecialError:
        # Evennia's reload test runner can import this module more than once.
        pass


class TestMobileSpecials(EvenniaTest):
    """Specials remain deterministic, data-only, and isolated per behavior."""

    def setUp(self):
        super().setUp()
        self.npc = create_object(Character, key="Specialist", location=self.room1)
        self.npc.db.is_player_character = False

    def test_assignment_rejects_unknown_duplicate_and_nonprimitive_data(self):
        with self.assertRaises(MobileSpecialError):
            validate_mobile_specials(
                {"version": 1, "behaviors": [{"key": "gone", "config": {}}]}
            )
        with self.assertRaises(MobileSpecialError):
            validate_mobile_specials(
                {
                    "version": 1,
                    "behaviors": [
                        {"key": "guard", "config": {}},
                        {"key": "guard", "config": {}},
                    ],
                }
            )
        with self.assertRaises(MobileSpecialError):
            validate_mobile_specials(
                {
                    "version": 1,
                    "behaviors": [{"key": "guard", "config": {"bad": object()}}],
                }
            )

    def test_priority_decline_and_one_action_budget_are_explicit(self):
        set_mobile_specials(
            self.npc,
            {
                "version": 1,
                "behaviors": [
                    {"key": "test.special.second", "config": {}},
                    {"key": "test.special.first", "config": {}},
                    {"key": "test.special.decline", "config": {}},
                ],
            },
        )

        result = dispatch_specials(self.npc, SpecialEvent("decision", token=1))

        self.assertEqual(
            result.outcomes,
            (
                ("test.special.decline", SpecialOutcome("declined", "nothing_to_do")),
                ("test.special.first", SpecialOutcome("acted", "marked")),
                ("test.special.second", SpecialOutcome("blocked", "action_consumed")),
            ),
        )
        self.assertEqual(self.npc.ndb.special_marks, ["decline", "first"])

    def test_bad_live_sibling_does_not_disable_valid_assignment(self):
        self.npc.attributes.add(
            MOBILE_SPECIALS_ATTRIBUTE,
            {
                "version": 1,
                "behaviors": [
                    {"key": "gone", "config": {}},
                    {"key": "test.special.first", "config": {}},
                    {"key": "test.special.first", "config": {}},
                ],
            },
        )

        result = dispatch_specials(self.npc, SpecialEvent("decision", token=1))

        self.assertTrue(result.acted)
        self.assertEqual(self.npc.ndb.special_marks, ["first"])

    def test_special_uses_the_mobile_token_before_existing_policy_actions(self):
        set_mobile_specials(
            self.npc,
            {"version": 1, "behaviors": [{"key": "test.special.first", "config": {}}]},
        )
        event = PulseEvent(1, PulseLane.MOBILES, 1)

        first = process_mobile_pulse(event, mobiles=(self.npc,))
        repeated = process_mobile_pulse(event, mobiles=(self.npc,))

        self.assertEqual(first.outcomes[0].reason, "special")
        self.assertEqual(repeated.outcomes[0].reason, "duplicate_token")
        self.assertEqual(self.npc.ndb.special_marks, ["first"])

"""MOB-08 regression coverage for non-mutating mobile inspection."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.mobile_diagnostics import (MOBILE_FAILURE_ATTRIBUTE,
                                        clear_mobile_failure,
                                        mark_mobile_failure_recovered,
                                        mobile_diagnostic_snapshot,
                                        record_mobile_failure)
from systems.mobiles import MOBILE_BEHAVIOR_ATTRIBUTE


class TestMobileDiagnostics(EvenniaTest):
    """Diagnostic reads retain safe evidence without altering mobile state."""

    def setUp(self):
        super().setUp()
        self.npc = create_object(
            "typeclasses.characters.Character",
            key="Diagnostic NPC",
            location=self.room1,
        )
        self.npc.db.is_player_character = False

    def test_snapshot_does_not_initialize_absent_mobile_state(self):
        """A Builder read must not create runner state on an old live NPC."""
        self.assertIsNone(self.npc.attributes.get(MOBILE_BEHAVIOR_ATTRIBUTE))

        snapshot = mobile_diagnostic_snapshot(self.npc)

        self.assertEqual(snapshot["kind"], "live")
        self.assertEqual(
            snapshot["sections"]["spawn"]["data"]["identity"], "untemplated"
        )
        self.assertEqual(snapshot["sections"]["runner"]["data"]["behavior_key"], "idle")
        self.assertIsNone(self.npc.attributes.get(MOBILE_BEHAVIOR_ATTRIBUTE))

    def test_failure_repeats_recovery_and_audited_clear(self):
        """Only safe primitive failure information is retained and coalesced."""
        record_mobile_failure(self.npc, "runner", "unknown_behavior", token=4)
        record_mobile_failure(self.npc, "runner", "unknown_behavior", token=4)
        mark_mobile_failure_recovered(self.npc)

        failure = self.npc.attributes.get(MOBILE_FAILURE_ATTRIBUTE)
        self.assertEqual(failure["version"], 1)
        self.assertEqual(failure["repeat_count"], 2)
        self.assertIsNotNone(failure["recovered_at"])
        self.char1.permissions.add("Builder")
        self.assertTrue(clear_mobile_failure(self.npc, audited_by=self.char1))
        self.assertIsNone(self.npc.attributes.get(MOBILE_FAILURE_ATTRIBUTE))

    def test_player_characters_are_not_mobile_diagnostic_targets(self):
        """A PC does not expose mobile internals through the service."""
        with self.assertRaisesRegex(ValueError, "Only live NPCs"):
            mobile_diagnostic_snapshot(self.char1)

    def test_off_grid_npc_retains_independent_diagnostic_sections(self):
        """A missing location is a finding, not a reason to hide the snapshot."""
        self.npc.location = None

        snapshot = mobile_diagnostic_snapshot(self.npc)

        self.assertIsNone(snapshot["identity"]["location_dbref"])
        self.assertIn("runner", snapshot["sections"])
        self.assertIn(
            "scheduled_off_grid", {item["reason"] for item in snapshot["findings"]}
        )

"""COMM-04A persistence, privacy, and abuse-limit coverage."""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from evennia.comms.models import Msg
from evennia.utils.test_resources import EvenniaTest
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY
# fmt: off
from systems.reports import (MAX_UNRESOLVED_REPORTS, REPORT_CATEGORY,
                             REPORT_TAG, metadata, normalize_body,
                             report_messages, submit)

# fmt: on


class TestReports(EvenniaTest):
    """Reports retain only the bounded snapshots needed for staff triage."""

    def setUp(self) -> None:
        super().setUp()
        self.room1.tags.add("starter", category=AREA_TAG_CATEGORY)
        self.room1.tags.add("square", category=ROOM_KEY_CATEGORY)

    def test_normalization_rejects_short_markup_control_and_overlong_text(self) -> None:
        """Player report bodies have their own 10–2,000 plain-text contract."""
        self.assertIsNone(normalize_body("short"))
        self.assertIsNone(normalize_body("adequate |rmarkup"))
        self.assertIsNone(normalize_body("adequate\nbody"))
        self.assertIsNone(normalize_body("x" * 2001))
        self.assertEqual(normalize_body("  A useful   report.  "), "A useful report.")

    def test_report_persists_safe_primitive_snapshots_and_survives_context_changes(
        self,
    ) -> None:
        """The record does not retain live objects or operational/client data."""
        with patch("systems.reports._puppet", return_value=self.char1):
            result = submit(self.account, "bug", "The northern exit does not open.")
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "submitted")
        self.assertEqual(len(result.report_id), 32)
        message = result.message
        self.assertEqual(message.message, "The northern exit does not open.")
        self.assertTrue(message.tags.has(REPORT_TAG, category=REPORT_CATEGORY))
        snapshot = metadata(message)
        self.assertEqual(snapshot["kind"], "bug")
        self.assertEqual(snapshot["account_id"], self.account.id)
        self.assertEqual(snapshot["character_id"], self.char1.id)
        self.assertEqual(snapshot["area_key"], "starter")
        self.assertEqual(snapshot["room_key"], "square")
        self.assertEqual(snapshot["room_dbref"], f"#{self.room1.id}")
        self.assertEqual(snapshot["build_id"], "development")
        forbidden = {
            "address",
            "password",
            "history",
            "inventory",
            "exception",
            "traceback",
            "filesystem",
            "environment",
            "secret",
            "room_object",
            "character_object",
        }
        self.assertFalse(forbidden.intersection(snapshot))
        original_room_name = snapshot["room_name"]
        self.room1.key = "Renamed room"
        self.char1.delete()
        self.assertEqual(metadata(message)["room_name"], original_room_name)

    def test_ooc_context_and_each_duplicate_receive_independent_stable_ids(
        self,
    ) -> None:
        """Reports work without a puppet and deliberately do not deduplicate."""
        with patch("systems.reports._puppet", return_value=None):
            first = submit(self.account, "idea", "Please add more forest trails.")
            second = submit(self.account, "idea", "Please add more forest trails.")
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertNotEqual(first.report_id, second.report_id)
        snapshot = metadata(first.message)
        self.assertIsNone(snapshot["character_id"])
        self.assertIsNone(snapshot["room_dbref"])

    def test_rolling_and_unresolved_limits_reject_without_writing(self) -> None:
        """The serial account lock prevents cap races from admitting an extra row."""
        with patch("systems.reports._puppet", return_value=None):
            for index in range(3):
                self.assertTrue(
                    submit(
                        self.account, "typo", f"A useful typo report {index}."
                    ).accepted
                )
            before = report_messages().count()
            self.assertFalse(
                submit(self.account, "typo", "A useful typo report four.").accepted
            )
            self.assertEqual(report_messages().count(), before)
            report_messages().filter(db_sender_accounts=self.account).update(
                db_date_created=timezone.now() - timedelta(minutes=11)
            )
            for index in range(MAX_UNRESOLVED_REPORTS - 3):
                result = submit(
                    self.account, "typo", f"Another useful typo report {index}."
                )
                report_messages().filter(pk=result.message.pk).update(
                    db_date_created=timezone.now() - timedelta(minutes=11)
                )
            before = report_messages().count()
            self.assertFalse(
                submit(
                    self.account, "typo", "This one exceeds unresolved limit."
                ).accepted
            )
            self.assertEqual(report_messages().count(), before)

    def test_storage_failure_leaves_no_partial_record(self) -> None:
        """A failed write has no externally visible report id or partial record."""
        with patch("systems.reports.Msg.objects.create_message", side_effect=Exception):
            result = submit(self.account, "bug", "The village gate is missing.")
        self.assertFalse(result.accepted)
        self.assertEqual(report_messages().count(), 0)
        self.assertTrue(
            submit(self.account, "bug", "The village gate is missing.").accepted
        )
        self.assertEqual(
            Msg.objects.latest("id").message, "The village gate is missing."
        )

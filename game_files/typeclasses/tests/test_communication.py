"""Focused coverage for COMM-01A's pure communication policy."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.communication import (
    MAX_COMMUNICATION_LENGTH,
    RATE_CAPACITY,
    authorize,
    normalize_text,
)


class TestCommunicationPolicy(EvenniaTest):
    """Keep untrusted text and transient rate behavior independent of commands."""

    def test_text_is_one_safe_bounded_logical_line(self):
        """Whitespace normalizes while controls, markup, empties, and excess fail."""
        self.assertEqual(normalize_text("  hello   there ").text, "hello there")
        self.assertFalse(normalize_text("\nhello").accepted)
        self.assertFalse(normalize_text("|rforged").accepted)
        self.assertFalse(normalize_text("   ").accepted)
        self.assertFalse(normalize_text("x" * (MAX_COMMUNICATION_LENGTH + 1)).accepted)

    @patch("systems.communication.monotonic")
    def test_rate_bucket_is_account_runtime_only_and_shout_costs_three(self, clock):
        """Five actions fit a window, then expire without a persistent attribute."""
        clock.return_value = 100.0
        for _ in range(RATE_CAPACITY):
            self.assertTrue(authorize(self.char1, "hello").accepted)
        self.assertEqual(authorize(self.char1, "again").reason, "rate_limited")
        self.account.ndb.communication_rate = None
        self.assertTrue(authorize(self.char1, "shout", cost=3).accepted)
        self.assertEqual(authorize(self.char1, "more", cost=3).reason, "rate_limited")
        clock.return_value = 111.0
        self.assertTrue(authorize(self.char1, "after window").accepted)
        self.assertIsNone(self.char1.attributes.get("communication_rate"))

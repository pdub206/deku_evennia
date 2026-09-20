"""GROUP-01A consent, bounds, and lifecycle regression coverage."""

from evennia.utils.test_resources import EvenniaTest
from systems.player_following import (
    accept,
    clear_for,
    decline,
    follow_state,
    request,
    unlink,
)


class TestPlayerFollowing(EvenniaTest):
    """PC follow state stores only ids and always requires consent."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.char2.move_to(self.room1, quiet=True)

    def test_request_accept_and_unlink(self):
        """A request changes to an edge only after the target accepts it."""
        self.assertTrue(request(self.char1, self.char2).accepted)
        self.assertEqual(follow_state(self.char1)["request_id"], self.char2.id)
        self.assertTrue(accept(self.char2, self.char1).accepted)
        self.assertEqual(follow_state(self.char1)["leader_id"], self.char2.id)
        self.assertIsNone(follow_state(self.char1)["request_id"])
        self.assertTrue(unlink(self.char1).accepted)
        self.assertIsNone(follow_state(self.char1)["leader_id"])

    def test_decline_and_lifecycle_clear_are_idempotent(self):
        """Requests and edges touching a departed PC end exactly safely."""
        request(self.char1, self.char2)
        self.assertTrue(decline(self.char2, self.char1).accepted)
        self.assertFalse(decline(self.char2, self.char1).accepted)
        request(self.char1, self.char2)
        accept(self.char2, self.char1)
        clear_for(self.char2)
        clear_for(self.char2)
        self.assertIsNone(follow_state(self.char1)["leader_id"])

"""GROUP-01A consent, bounds, and lifecycle regression coverage."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.action_queue import inspect_action, process_action_pulse
from systems.player_following import (
    accept,
    clear_for,
    decline,
    follow_state,
    request,
    unlink,
)
from systems.pulses import PulseEvent, PulseLane
from typeclasses.exits import Exit


class TestPlayerFollowing(EvenniaTest):
    """PC follow state stores only ids and always requires consent."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.char2.move_to(self.room1, quiet=True)
        self.exit = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )

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

    def test_committed_exit_travel_queues_direct_follower_with_own_delay(self):
        """A leader's committed traversal, not its command, fans out one edge."""
        self.char1.db.speed = 30
        self.char2.db.speed = 15
        request(self.char2, self.char1)
        accept(self.char1, self.char2)

        self.exit.at_traverse(self.char1, self.room2)
        leader_due = inspect_action(self.char1)["due_token"]
        process_action_pulse(PulseEvent(leader_due, PulseLane.ACTIONS, leader_due))

        follower_action = inspect_action(self.char2)
        self.assertIs(self.char1.location, self.room2)
        self.assertIs(self.char2.location, self.room1)
        self.assertEqual(follower_action["definition"], "interact03.travel")
        self.assertEqual(follower_action["due_token"], leader_due + 2)

        follower_due = follower_action["due_token"]
        process_action_pulse(PulseEvent(follower_due, PulseLane.ACTIONS, follower_due))
        self.assertIs(self.char2.location, self.room2)
        self.assertEqual(follow_state(self.char2)["leader_id"], self.char1.id)

    def test_blocked_follower_is_unlinked_without_blocking_other_edges(self):
        """One bad follower edge does not prevent a sibling from being queued."""
        third = create_object(
            "typeclasses.characters.Character", key="third", location=self.room1
        )
        third.db.is_player_character = True
        request(self.char2, self.char1)
        accept(self.char1, self.char2)
        request(third, self.char1)
        accept(self.char1, third)
        self.char2.db.position = "sitting"

        self.exit.at_traverse(self.char1, self.room2)
        due = inspect_action(self.char1)["due_token"]
        process_action_pulse(PulseEvent(due, PulseLane.ACTIONS, due))

        self.assertIsNone(follow_state(self.char2)["leader_id"])
        self.assertEqual(follow_state(third)["leader_id"], self.char1.id)
        self.assertIsNotNone(inspect_action(third))

    def test_forced_relocation_clears_only_separated_follow_edge(self):
        """Direct movement never propagates or leaves a stale catch-up link."""
        request(self.char2, self.char1)
        accept(self.char1, self.char2)

        self.char1.move_to(self.room2, quiet=True, move_type="teleport")

        self.assertIs(self.char2.location, self.room1)
        self.assertIsNone(follow_state(self.char2)["leader_id"])

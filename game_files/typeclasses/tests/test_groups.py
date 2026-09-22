"""Regression coverage for GROUP-02A's durable party registry."""

from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems import groups
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    UnavailabilityCause,
)


class TestGroups(EvenniaTest):
    """Exercise durable membership independently of command presentation."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.filter(db_key=groups.GROUP_CONFIG_KEY).delete()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.char1.key = "Leader"
        self.char2.key = "Invitee"
        self.char1.db.senses = ["darkvision"]
        self.char2.db.senses = ["darkvision"]
        self.char1.location = self.room1
        self.char2.location = self.room1

    def test_invite_accept_and_leader_transfer(self):
        """An accepted invitation creates a stable leader-led party."""
        self.assertTrue(groups.invite(self.char1, self.char2).accepted)
        accepted = groups.accept(self.char2, self.char1)
        self.assertTrue(accepted.accepted)
        party = groups.group_for(self.char1)
        self.assertEqual(party["leader_id"], self.char1.id)
        self.assertEqual(party["members"], [self.char1.id, self.char2.id])
        self.assertTrue(groups.transfer_leader(self.char1, self.char2).accepted)
        self.assertEqual(groups.group_for(self.char1)["leader_id"], self.char2.id)

    def test_leader_cannot_leave_without_transfer(self):
        """Leaving cannot silently select a new leader."""
        groups.invite(self.char1, self.char2)
        groups.accept(self.char2, self.char1)
        outcome = groups.leave(self.char1)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "leader_must_transfer")

    def test_deleted_leader_promotes_earliest_join(self):
        """Permanent deletion uses the persisted join order for succession."""
        groups.invite(self.char1, self.char2)
        groups.accept(self.char2, self.char1)
        groups.remove_deleted(self.char1)
        party = groups.group_for(self.char2)
        self.assertEqual(party["leader_id"], self.char2.id)
        self.assertEqual(party["members"], [self.char2.id])

    def test_final_disconnect_ends_invitation_but_preserves_membership(self):
        """Only a final lifecycle transition clears an offer, never the party."""
        self.assertTrue(groups.invite(self.char1, self.char2).accepted)
        groups._on_character_lifecycle(
            CharacterLifecycleEvent(
                self.char1,
                CharacterAvailability.UNAVAILABLE,
                1,
                UnavailabilityCause.DISCONNECT,
            )
        )
        self.assertEqual(groups._read()["invitations"], [])

        self.assertTrue(groups.invite(self.char1, self.char2).accepted)
        self.assertTrue(groups.accept(self.char2, self.char1).accepted)
        groups._on_character_lifecycle(
            CharacterLifecycleEvent(
                self.char1,
                CharacterAvailability.UNAVAILABLE,
                2,
                UnavailabilityCause.OOC,
            )
        )
        self.assertEqual(
            groups.group_members(self.char1), (self.char1.id, self.char2.id)
        )

    def test_status_is_ordered_and_does_not_disclose_inaccessible_room(self):
        """Status uses consented labels while respecting the room's view lock."""
        self.assertTrue(groups.invite(self.char1, self.char2).accepted)
        self.assertTrue(groups.accept(self.char2, self.char1).accepted)
        self.room1.locks.add("view:false()")

        status = groups.status_lines(self.char1)

        self.assertIsNotNone(status)
        self.assertEqual(status[0], "Leader: Leader")
        self.assertIn("Location unavailable", status[1])
        self.assertIn("Location unavailable", status[2])
        self.assertNotIn("#", "\n".join(status))

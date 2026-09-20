"""Regression coverage for GROUP-02A's durable party registry."""

from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest

from systems import groups


class TestGroups(EvenniaTest):
    """Exercise durable membership independently of command presentation."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.filter(db_key=groups.GROUP_CONFIG_KEY).delete()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
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

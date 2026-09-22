"""COMM-01C bootstrap and bounded-history coverage."""

from unittest.mock import patch

from evennia.comms.models import ChannelDB
from evennia.server.models import ServerConfig
from evennia.utils import create
from evennia.utils.test_resources import EvenniaTest
from systems.channels import (
    CHANNEL_HISTORY_LIMIT,
    reconcile_channels,
    released_channels,
)


class TestCuratedChannels(EvenniaTest):
    """Verify startup migration is safe and released history remains bounded."""

    def setUp(self):
        super().setUp()
        ChannelDB.objects.all().delete()
        ServerConfig.objects.conf("comm_01c_existing_accounts_subscribed", delete=True)

    def test_reconcile_renames_unique_public_in_place_and_subscribes_once(self):
        """The stock channel keeps its identity while Public becomes OOC."""
        public = create.create_channel(
            "Public", typeclass="typeclasses.channels.Channel"
        )
        original_id = public.id
        reports = reconcile_channels()
        self.assertTrue(any("renamed stock Public" in report for report in reports))
        self.assertEqual(ChannelDB.objects.get(id=original_id).key, "OOC")
        self.assertEqual(
            [channel.key for channel in released_channels()], ["OOC", "Newbie"]
        )
        self.assertTrue(
            ChannelDB.objects.get(id=original_id).has_connection(self.account)
        )
        ChannelDB.objects.get(id=original_id).disconnect(self.account)
        reconcile_channels()
        self.assertFalse(
            ChannelDB.objects.get(id=original_id).has_connection(self.account)
        )

    def test_history_retains_the_latest_two_hundred_messages(self):
        """The channel does not use an unbounded file or message history."""
        reconcile_channels()
        channel = next(
            channel for channel in released_channels() if channel.key == "OOC"
        )
        channel.connect(self.account)
        with patch.object(channel.subscriptions, "online", return_value=[]):
            for number in range(CHANNEL_HISTORY_LIMIT + 1):
                channel.msg(f"message {number}", senders=self.account)
        self.assertEqual(len(channel.db.comm_history), CHANNEL_HISTORY_LIMIT)
        self.assertEqual(channel.db.comm_history[0]["text"], "message 1")

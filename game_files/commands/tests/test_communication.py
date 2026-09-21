"""Command-level coverage for COMM-01A local speech and COMM-01B tells."""

from unittest.mock import MagicMock, patch

# fmt: off
from commands.communication import (CmdAnnounce, CmdAsk, CmdChannel, CmdIgnore,
                                    CmdSay, CmdShout, CmdTell, CmdWhisper)
# fmt: on
from evennia import create_object
from evennia.comms.models import ChannelDB, Msg
from evennia.utils import create
from evennia.utils.test_resources import EvenniaCommandTest


class TestCommunicationCommands(EvenniaCommandTest):
    """Exercise command parsing and the shared in-character action hook."""

    def setUp(self):
        super().setUp()
        self.char1.db.languages = ["Common"]
        self.char1.db.active_language = "Common"
        self.char2.db.languages = ["Common"]
        self.char2.move_to(self.room1, quiet=True)

    def test_local_speech_surfaces(self):
        """Say, private whisper, audible ask, and shout use their released forms."""
        self.call(CmdSay(), "hello", 'You say, in common,\n  "hello"')
        self.call(
            CmdWhisper(),
            f"{self.char2.key} secret",
            f'You whisper to {self.char2.key}, in common,\n  "secret"',
        )
        self.call(
            CmdAsk(),
            f"{self.char2.key} ready",
            f'You ask {self.char2.key}, in common,\n  "ready"',
        )
        self.account.ndb.communication_rate = None
        self.call(CmdShout(), "hello", 'You shout, in common,\n  "hello"')

    def test_shout_reaches_an_open_one_way_exit_and_speech_obeys_posture(self):
        """Topology is nonrecursive and sleeping actors are denied by action policy."""
        create_object(
            "typeclasses.exits.Exit",
            key="north",
            location=self.room1,
            destination=self.room2,
        )
        self.call(CmdShout(), "across", 'You shout, in common,\n  "across"')
        self.char1.db.position = "sleeping"
        self.call(
            CmdSay(),
            "hello",
            "You are asleep and cannot do that. Type wake to wake up.",
        )

    def test_tell_requires_an_online_target_and_never_creates_history(self):
        """Offline targets fail safely; live tells are direct, transient delivery."""
        self.call(
            CmdTell(),
            f"{self.account2.key} hello",
            "That person is unavailable.",
            caller=self.account,
        )
        before = Msg.objects.count()
        with patch.object(self.account2.sessions, "count", return_value=1):
            self.call(
                CmdTell(),
                f"{self.account2.key} hello",
                {
                    self.account: f'You tell {self.account2.key}, "hello"',
                    self.account2: f'{self.account.key} tells you, "hello"',
                },
                caller=self.account,
            )
            self.call(
                CmdTell(),
                "/reply again",
                {
                    self.account: f'You tell {self.account2.key}, "again"',
                    self.account2: f'{self.account.key} tells you, "again"',
                },
                caller=self.account,
            )
        self.assertEqual(Msg.objects.count(), before)
        self.call(
            CmdTell(),
            "/list 10",
            'tell: Extra switch "/list" ignored.|Usage: tell <account> <message>',
            caller=self.account,
        )

    def test_ignore_blocks_tells_and_persists_only_account_ids(self):
        """Ignore has bounded persistent ids and hides tell-target details."""
        self.call(
            CmdIgnore(),
            f"/add {self.account2.key}",
            f"You now ignore {self.account2.key}.",
            caller=self.account,
        )
        self.assertEqual(self.account.db.ignored_account_ids, [self.account2.id])
        with patch.object(self.account.sessions, "count", return_value=1):
            self.call(
                CmdTell(),
                f"{self.account.key} hello",
                "That person is unavailable.",
                caller=self.account2,
            )
        self.call(
            CmdIgnore(),
            f"/remove {self.account2.key}",
            f"You no longer ignore {self.account2.key}.",
            caller=self.account,
        )

    def test_channel_applies_rate_ignore_and_subscription_controls(self):
        """Released channels are account-wide, mute separately, and honor ignore."""
        ChannelDB.objects.all().delete()
        channel = create.create_channel(
            "OOC",
            typeclass="typeclasses.channels.Channel",
            locks="listen:all();send:all()",
        )
        channel.connect(self.account)
        channel.connect(self.account2)
        self.account2.msg = MagicMock()
        self.account2.db.ignored_account_ids = [self.account.id]
        with patch.object(
            channel.subscriptions, "online", return_value=[self.account, self.account2]
        ):
            self.call(
                CmdChannel(),
                "OOC = hello",
                f"[OOC] {self.account.key}: hello",
                caller=self.account,
            )
        self.account2.msg.assert_not_called()
        self.assertEqual(
            channel.db.comm_history[-1], {"sender": self.account.key, "text": "hello"}
        )
        self.call(CmdChannel(), "/mute OOC", "Muted channel OOC.", caller=self.account)
        self.call(
            CmdChannel(), "/unmute OOC", "Un-muted channel OOC.", caller=self.account
        )
        self.call(
            CmdChannel(),
            "/unsub OOC",
            "You unsubscribed from OOC.",
            caller=self.account,
        )

    def test_announce_bypasses_player_filters_and_is_not_channel_history(self):
        """Announcements bypass player ignore/mute and never become channel text."""
        self.account.permissions.add("Admin")
        self.account.msg = MagicMock()
        self.account2.msg = MagicMock()
        with (
            patch.object(self.account.sessions, "count", return_value=1),
            patch.object(self.account2.sessions, "count", return_value=1),
        ):
            command = CmdAnnounce()
            command.caller = self.account
            command.args = "hello"
            command.func()
        self.account.msg.assert_called_once_with("|r[ANNOUNCEMENT]|n hello")
        self.account2.msg.assert_called_once_with("|r[ANNOUNCEMENT]|n hello")

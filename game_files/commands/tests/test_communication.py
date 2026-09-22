"""Command-level coverage for COMM-01A local speech and COMM-01B tells."""

from unittest.mock import MagicMock, patch

from commands.boards import CmdBoard
from commands.boards import _editor_quit as board_editor_quit
# fmt: off
from commands.communication import (CmdAnnounce, CmdAsk, CmdChannel, CmdIgnore,
                                    CmdMail, CmdSay, CmdShout, CmdTell,
                                    CmdWhisper, _mail_editor_quit)
from commands.reports import CmdReport
from commands.socials import CmdSocial, CmdSocials
# fmt: on
from evennia import create_object
from evennia.comms.models import ChannelDB, Msg
from evennia.utils import create
from evennia.utils.test_resources import EvenniaCommandTest
from systems.reports import ReportResult
from systems.socials import SOCIALS


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

    def test_mail_composes_in_editor_then_persists_and_marks_read(self):
        """COMM-03A accepts no inline body and sends the editor buffer on exit."""
        before = Msg.objects.count()
        command = CmdMail()
        command.caller = self.account
        command.switches = ["send"]
        command.args = f"{self.account2.key} = Greetings"
        command.lhs = self.account2.key
        command.rhs = "Greetings"
        with patch("commands.communication.EvEditor") as editor:
            command.func()
        editor.assert_called_once()
        self.account.ndb._eveditor = MagicMock(_buffer="A durable\nmail body.")
        _mail_editor_quit(self.account)
        self.assertEqual(Msg.objects.count(), before + 1)
        message = Msg.objects.latest("id")
        self.assertEqual(message.header, "Greetings")
        self.assertEqual(message.message, "A durable\nmail body.")
        self.assertTrue(message.tags.has("deku_mail", category="communication"))
        self.assertTrue(message.tags.has("unread", category="communication"))
        self.call(
            CmdMail(),
            f"/read {message.id}",
            f"Mail #{message.id} from {self.account.key}: Greetings\n\nA durable\nmail body.",
            caller=self.account2,
        )
        self.assertFalse(message.tags.has("unread", category="communication"))

    def test_mail_hides_only_one_view_and_blocks_ignored_recipient(self):
        """Mail deletion is independent and ignore produces the generic denial."""
        from systems.mail import send

        result = send(self.account, self.account2, "Subject", "Body")
        self.assertTrue(result.accepted)
        message = result.message
        self.call(
            CmdMail(), f"/delete {message.id}", "Mail deleted.", caller=self.account
        )
        self.call(CmdMail(), "", "You have no mail on that page.", caller=self.account)
        self.call(
            CmdMail(),
            "",
            f"Inbox (1 unread), page 1:\n*{message.id:>5} {self.account.key}: Subject",
            caller=self.account2,
        )
        self.account2.db.ignored_account_ids = [self.account.id]
        denied = send(self.account, self.account2, "Another", "Body")
        self.assertFalse(denied.accepted)
        self.assertEqual(denied.reason, "unavailable")

    def test_board_posts_through_editor_and_read_advances_high_water(self):
        """COMM-03B posts the editor body with local numbering and public reads."""
        command = CmdBoard()
        command.caller = self.account
        command.switches = ["post"]
        command.args = "general A subject"
        with patch("commands.boards.EvEditor") as editor:
            command.func()
        editor.assert_called_once()
        self.account.ndb._eveditor = MagicMock(_buffer="A board\nbody.")
        board_editor_quit(self.account)
        from systems.boards import posts

        message = posts("general")[0]
        self.assertEqual(message.header, "A subject")
        self.call(
            CmdBoard(),
            "/read general 1",
            "General #1 by %s: A subject\n\nA board\nbody." % self.account.key,
            caller=self.account2,
        )
        self.assertEqual(self.account2.db.board_read_high_water, {"general": 1})

    def test_board_news_is_admin_only_and_general_author_can_remove(self):
        """News and moderation remain Admin-only while authors own general removal."""
        from systems.boards import post, posts

        self.assertFalse(post(self.account2, "news", "No", "Body").accepted)
        posted = post(self.account2, "general", "Mine", "Body")
        self.assertTrue(posted.accepted)
        self.call(
            CmdBoard(), "/remove general 1", "Post removed.", caller=self.account2
        )
        self.assertEqual(posts("general"), [])

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

    def test_report_commands_are_account_scoped_and_acknowledge_only_id_status(self):
        """Bug, typo, and idea remain available OOC and reveal no saved context."""
        with patch(
            "commands.reports.submit",
            return_value=ReportResult(True, "ok", "safe-report-id", "submitted"),
        ):
            self.call(
                CmdReport(),
                "The gate description has a spelling error.",
                "Report #safe-report-id submitted.",
                caller=self.account,
            )
        self.call(
            CmdReport(),
            "short",
            "Reports need 10–2,000 characters of plain text.",
            caller=self.account,
        )


class TestSocialCommands(EvenniaCommandTest):
    """COMM-02 reaches fixed socials through one action-policy-aware command."""

    def setUp(self):
        super().setUp()
        self.char2.move_to(self.room1, quiet=True)

    def test_registry_command_lists_and_delivers_no_target_social(self):
        """The generic command is registered under every release verb in order."""
        self.call(
            CmdSocials(),
            "",
            "Available socials: bow, grin, laugh, nod, salute, shake, shrug, sigh, smile, thank, wave, wink.",
        )
        for key in SOCIALS:
            self.account.ndb.communication_rate = None
            self.call(CmdSocial(), "", f"You {key}.", cmdstring=key)

    def test_directed_and_self_socials_have_distinct_exact_audiences(self):
        """A visible target receives a direct response; self has only observers."""
        self.call(
            CmdSocial(),
            self.char2.key,
            {
                self.char1: f"You bow to {self.char2.key}.",
                self.char2: f"{self.char1.key} bows to you.",
            },
        )
        self.account.ndb.communication_rate = None
        self.call(CmdSocial(), self.char1.key, "You bow to yourself.")

    def test_social_target_and_observer_ignore_are_private_denials(self):
        """A direct target blocks before broadcast and ignoring observers hear nothing."""
        self.char2.db.account = self.account2
        self.account2.db.ignored_account_ids = [self.account.id]
        self.call(CmdSocial(), self.char2.key, "That person is unavailable.")
        self.account2.db.ignored_account_ids = None
        self.account.ndb.communication_rate = None
        self.char2.msg = MagicMock()
        self.account2.db.ignored_account_ids = [self.account.id]
        self.call(CmdSocial(), "", "You bow.")
        self.char2.msg.assert_not_called()

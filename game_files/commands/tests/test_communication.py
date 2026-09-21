"""Command-level coverage for COMM-01A's local speech verbs."""

from commands.communication import CmdAsk, CmdSay, CmdShout, CmdWhisper
from evennia import create_object
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

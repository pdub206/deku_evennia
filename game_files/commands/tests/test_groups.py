"""Command-level coverage for GROUP-02A party management."""

from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaCommandTest

from commands.groups import CmdGroup
from systems import groups


class TestCmdGroup(EvenniaCommandTest):
    """The command delegates membership authority to the registry."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.filter(db_key=groups.GROUP_CONFIG_KEY).delete()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.char1.key = "Leader"
        self.char2.key = "Invitee"
        self.char1.db.senses = ["darkvision"]
        self.char2.db.senses = ["darkvision"]
        self.char2.move_to(self.room1, quiet=True)

    def test_invite_and_display_party(self):
        """An invite uses the local visible target and group displays members."""
        self.call(CmdGroup(), "invite Invitee", "You invite Invitee.")
        self.call(CmdGroup(), "accept Leader", "You join Leader's group.", caller=self.char2)
        self.call(CmdGroup(), "", "Leader: Leader\nMembers: Leader, Invitee")

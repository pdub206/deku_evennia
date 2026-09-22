"""Coverage for COMM-05's bounded public-information command surface."""

from types import SimpleNamespace
from unittest.mock import patch

from commands.information import (
    CmdCredits,
    CmdInfo,
    CmdSessions,
    CmdWhere,
    CmdWho,
    CmdWizlist,
    INFORMATION_TAG_CATEGORY,
    PUBLIC_WIZLIST_TAG,
    _connected_accounts,
)
from evennia.utils.test_resources import EvenniaCommandTest
from systems import groups


class TestInformationCommands(EvenniaCommandTest):
    """Assert public lists stay bounded while staff diagnostics remain locked."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char2.db.is_player_character = True
        self.char1.db.senses = ["darkvision"]
        self.char2.db.senses = ["darkvision"]
        self.char1.move_to(self.room1, quiet=True)
        self.char2.move_to(self.room1, quiet=True)

    def test_connected_accounts_deduplicate_multiple_sessions(self):
        """One Account with two live sessions produces one public row."""
        first = SimpleNamespace(account=self.account)
        second = SimpleNamespace(account=self.account)
        with patch(
            "commands.information.evennia.SESSION_HANDLER.get_sessions",
            return_value=[first, second],
        ):
            self.assertEqual(_connected_accounts(), [(self.account, [first, second])])

    def test_where_is_self_or_consented_group_locations_only(self):
        """Where uses the group safe-room reader instead of global lookup."""
        self.call(CmdWhere(), "", "Where:\nChar", caller=self.char1)
        self.assertTrue(groups.invite(self.char1, self.char2).accepted)
        self.assertTrue(groups.accept(self.char2, self.char1).accepted)
        self.char2.move_to(self.room2, quiet=True)
        self.call(CmdWhere(), "", "Where:\nChar", caller=self.char1)
        self.room2.locks.add("view:false()")
        self.assertIn(
            "Location unavailable", "\n".join(groups.location_lines(self.char1))
        )

    def test_public_commands_and_staff_list_do_not_expose_session_data(self):
        """Public commands are configured/static; wizlist requires a staff opt-in."""
        self.call(
            CmdInfo(),
            "",
            "game\nRelease: development\nVersion: Evennia\n"
            "Transports: telnet and web client\nRules: SRD-inspired fantasy adventure\n"
            "Help: help <topic>\nContact: Contact a staff member in game.",
            caller=self.account,
        )
        self.call(
            CmdCredits(),
            "",
            "This game is built with Evennia.\n"
            "Rules inspiration includes the System Reference Document (SRD).\n"
            "See the project source and in-game staff for additional credits.",
            caller=self.account,
        )
        self.call(
            CmdWizlist(),
            "",
            "No staff have opted into the public list.",
            caller=self.account,
        )
        self.account.permissions.add("Admin")
        self.account.tags.add(PUBLIC_WIZLIST_TAG, category=INFORMATION_TAG_CATEGORY)
        self.account.attributes.add("public_wizlist_role", "Administrator")
        self.call(
            CmdWizlist(),
            "",
            "Staff list (page 1/1):\n\nRole          Name        \n"
            "Administrator TestAccount",
            caller=self.account,
        )
        self.assertEqual(CmdSessions().locks, "cmd:perm(Admin)")

    def test_who_usage_is_bounded(self):
        """Who accepts only an optional positive page rather than query filters."""
        self.call(CmdWho(), "not-a-page", "Usage: who [page]", caller=self.account)

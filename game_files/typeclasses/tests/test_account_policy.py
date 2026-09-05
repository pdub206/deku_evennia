"""Tests for WORLD-04 single-character ownership and exclusive puppeting."""

from unittest.mock import MagicMock, patch

from commands.account import CmdCharCreate
from django.conf import settings
from evennia.contrib.rpg.character_creator.character_creator import \
    ContribChargenAccount
from evennia.utils.test_resources import EvenniaTest


class TestSingleCharacterOwnership(EvenniaTest):
    """Persistent ownership is limited at command, handler, and account layers."""

    def test_settings_enable_multiple_ooc_sessions_but_one_character(self):
        self.assertEqual(settings.MAX_NR_CHARACTERS, 1)
        self.assertEqual(settings.MULTISESSION_MODE, 2)
        self.assertEqual(settings.MAX_NR_SIMULTANEOUS_PUPPETS, 1)

    def test_first_creation_succeeds_and_second_is_rejected(self):
        first, errors = self.account2.create_character(key="Only", location=None)
        second, second_errors = self.account2.create_character(
            key="Extra", location=None
        )

        self.assertIsNotNone(first)
        self.assertFalse(errors)
        self.assertIsNone(second)
        self.assertEqual(second_errors, [self.account2.character_limit_message])
        self.assertEqual(list(self.account2.characters), [first])

    def test_direct_handler_add_rejects_second_character(self):
        self.account2.characters.add(self.char1)

        with self.assertRaisesRegex(RuntimeError, "exactly one character"):
            self.account2.characters.add(self.char2)

        self.assertEqual(list(self.account2.characters), [self.char1])

    def test_chargen_command_resumes_wip_but_hides_after_completion(self):
        command = CmdCharCreate()
        command.account = self.account2
        command.session = self.session
        command.msg = MagicMock()
        self.account2.characters.add(self.char2)

        command.func()

        command.msg.assert_called_once_with(self.account2.character_limit_message)


class TestExclusivePuppeting(EvenniaTest):
    """A second session cannot take over or disturb the sole owned PC."""

    def setUp(self):
        super().setUp()
        self.account.characters.add(self.char1)

    def test_first_puppet_reaches_evennia_mutation(self):
        session = MagicMock(puppet=None)
        self.char1.sessions.all = MagicMock(return_value=[])

        with patch.object(ContribChargenAccount, "puppet_object") as base_puppet:
            self.account.puppet_object(session, self.char1)

        base_puppet.assert_called_once_with(session, self.char1)

    def test_second_controller_is_rejected_without_displacement(self):
        active = MagicMock()
        challenger = MagicMock(puppet=None)
        self.char1.sessions.all = MagicMock(return_value=[active])

        with patch.object(ContribChargenAccount, "puppet_object") as base_puppet:
            with self.assertRaisesRegex(RuntimeError, "already controlled"):
                self.account.puppet_object(challenger, self.char1)

        base_puppet.assert_not_called()
        self.assertIsNone(challenger.puppet)
        self.assertEqual(self.char1.sessions.all(), [active])

    def test_malformed_legacy_ownership_fails_closed(self):
        self.account.ndb._world04_ownership_override = True
        self.account.characters.add(self.char2)
        self.account.ndb._world04_ownership_override = False

        with self.assertRaisesRegex(RuntimeError, "ownership is invalid"):
            self.account.puppet_object(MagicMock(puppet=None), self.char1)

    def test_reload_reconstruction_restores_same_session_references(self):
        session = MagicMock(puppet=None)
        self.char1.sessions.all = MagicMock(return_value=[session])

        with patch.object(ContribChargenAccount, "puppet_object") as base_puppet:
            self.account.puppet_object(session, self.char1)

        base_puppet.assert_not_called()
        self.assertEqual(session.puid, self.char1.id)
        self.assertEqual(session.puppet, self.char1)

    def test_non_owned_npc_requires_explicit_developer_path(self):
        npc = self.char2
        npc.db.is_player_character = False
        session = MagicMock(puppet=None)
        npc.sessions.all = MagicMock(return_value=[])

        with self.assertRaisesRegex(RuntimeError, "ownership is invalid"):
            self.account.puppet_object(session, npc)

        with patch.object(ContribChargenAccount, "puppet_object") as base_puppet:
            self.account.puppet_object_for_admin(session, npc)

        base_puppet.assert_called_once_with(session, npc)

    def test_admin_path_is_lock_gated(self):
        session = MagicMock(puppet=None)

        with self.assertRaisesRegex(RuntimeError, "Developer permission"):
            self.account2.puppet_object_for_admin(session, self.char1)

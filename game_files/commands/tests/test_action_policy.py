"""Command integration tests for the WORLD-02 action policy."""

from unittest.mock import MagicMock, patch

from commands.command import Command, MuxCommand
from commands.default_cmdsets import CharacterCmdSet
from commands.generic import (CmdAccess, CmdDrop, CmdGet, CmdGive, CmdHelp,
                              CmdHome, CmdInventory, CmdNick, CmdSetDesc)
from commands.position import CmdRest, CmdSit, CmdSleep, CmdStand, CmdWake
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.action_policy import ActionCategory, Position
from typeclasses.characters import Character
from typeclasses.exits import ExitCommand


class _ManipulateCommand(Command):
    """Record execution of a normal project command."""

    key = "test-manipulate"

    def func(self) -> None:
        self.caller.ndb.test_action_ran = True


class _ObserveMuxCommand(MuxCommand):
    """Record execution of a wrapped Evennia-style command."""

    key = "test-observe"
    aliases = ["test-observe-alias"]
    action_category = ActionCategory.OBSERVE

    def func(self) -> None:
        self.caller.ndb.test_action_ran = True


class _IndependentCommand(Command):
    """Record execution of a state-independent command."""

    key = "test-independent"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        self.caller.ndb.test_action_ran = True


class _UnclassifiedMuxCommand(MuxCommand):
    """Represent an account or staff Mux command with no IC action category."""

    key = "test-ooc"

    def func(self) -> None:
        self.caller.ndb.test_action_ran = True


class TestCommandPolicy(EvenniaCommandTest):
    """Both project command bases enforce declared action metadata."""

    def setUp(self):
        super().setUp()
        self.char1.ndb.test_action_ran = False

    def test_project_command_is_denied_once_without_side_effect(self):
        self.char1.db.position = "sleeping"

        output = self.call(_ManipulateCommand(), "")

        self.assertEqual(
            output,
            "You are asleep and cannot do that. Type wake to wake up.",
        )
        self.assertFalse(self.char1.ndb.test_action_ran)

    def test_mux_command_and_alias_share_policy(self):
        self.char1.db.position = "sleeping"

        output = self.call(_ObserveMuxCommand(), "", cmdstring="test-observe-alias")

        self.assertEqual(
            output,
            "You are asleep and cannot do that. Type wake to wake up.",
        )
        self.assertFalse(self.char1.ndb.test_action_ran)

    def test_state_independent_and_unclassified_commands_remain_available(self):
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=[Position.DEAD],
        ):
            self.call(_IndependentCommand(), "")
            self.assertTrue(self.char1.ndb.test_action_ran)
            self.char1.ndb.test_action_ran = False
            self.call(_UnclassifiedMuxCommand(), "")

        self.assertTrue(self.char1.ndb.test_action_ran)

    def test_item_wrapper_blocks_fighting_before_side_effect(self):
        item = create_object(
            "typeclasses.objects.Item", key="a stone", location=self.room1
        )
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=[Position.FIGHTING],
        ):
            output = self.call(CmdGet(), "stone")

        self.assertEqual(output, "You cannot do that while fighting.")
        self.assertEqual(item.location, self.room1)

    def test_existing_inventory_override_uses_central_policy(self):
        self.char1.db.position = "sleeping"

        self.call(CmdInventory(), "", "You are asleep")

    def test_character_cmdset_replaces_item_defaults_with_policy_wrappers(self):
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        expected = {"get": CmdGet, "drop": CmdDrop, "give": CmdGive}

        for key, command_type in expected.items():
            matches = [command for command in cmdset.commands if command.key == key]
            self.assertEqual(len(matches), 1, key)
            self.assertIsInstance(matches[0], command_type)

    def test_character_cmdset_classifies_every_general_player_command(self):
        """Inherited player commands cannot silently bypass action policy."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        expected = {
            "home": CmdHome,
            "nick": CmdNick,
            "setdesc": CmdSetDesc,
            "access": CmdAccess,
            "help": CmdHelp,
        }

        for key, command_type in expected.items():
            matches = [command for command in cmdset.commands if command.key == key]
            self.assertEqual(len(matches), 1, key)
            self.assertIsInstance(matches[0], command_type)
            self.assertIsNotNone(matches[0].action_category)

    def test_home_uses_movement_policy(self):
        self.char1.db.position = "sleeping"

        output = self.call(CmdHome(), "")

        self.assertEqual(
            output, "You are asleep and cannot do that. Type wake to wake up."
        )


class TestPositionCommands(EvenniaCommandTest):
    """Player commands use the validated posture transition API."""

    def test_complete_awake_sleep_wake_sequence(self):
        self.call(CmdSit(), "", "You sit down.")
        self.assertEqual(self.char1.db.position, "sitting")
        self.call(CmdRest(), "", "You lie down and rest.")
        self.assertEqual(self.char1.db.position, "resting")
        self.call(CmdStand(), "", "You stand up.")
        self.assertEqual(self.char1.db.position, "standing")
        self.call(CmdSleep(), "", "You fall asleep.")
        self.assertEqual(self.char1.db.position, "sleeping")
        self.call(CmdWake(), "", "You wake up, finding yourself sitting.")
        self.assertEqual(self.char1.db.position, "sitting")

    def test_idempotent_position_attempt(self):
        self.char1.db.position = "sitting"

        self.call(CmdSit(), "", "You are already sitting.")

        self.assertEqual(self.char1.db.position, "sitting")

    def test_sleeping_cannot_bypass_wake_with_stand(self):
        self.char1.db.position = "sleeping"

        self.call(CmdStand(), "", "You are asleep")

        self.assertEqual(self.char1.db.position, "sleeping")

    def test_fighting_blocks_posture_change(self):
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=[Position.FIGHTING],
        ):
            self.call(CmdRest(), "", "You cannot do that while fighting.")

        self.assertEqual(self.char1.db.position, "standing")

    def test_wake_is_denied_while_awake(self):
        self.call(CmdWake(), "", "You can only wake while sleeping.")


class TestExitCommandPolicy(EvenniaCommandTest):
    """Generated exits enforce movement before calling traversal hooks."""

    def setUp(self):
        super().setUp()
        self.exit = create_object(
            "typeclasses.exits.Exit",
            key="north",
            location=self.room1,
            destination=self.room2,
        )

    def test_generated_exit_denial_is_exactly_once(self):
        self.char1.db.position = "resting"

        output = self.call(ExitCommand(), "", obj=self.exit)

        self.assertEqual(output, "You need to stand before you can do that.")
        self.assertEqual(self.char1.location, self.room1)

    def test_exit_object_generates_the_policy_aware_command(self):
        cmdset = self.exit.create_exit_cmdset(self.exit)

        self.assertEqual(len(cmdset.commands), 1)
        self.assertIsInstance(cmdset.commands[0], ExitCommand)

    def test_direct_exit_traversal_denies_once(self):
        self.char1.db.position = "resting"
        self.char1.msg = MagicMock()

        self.exit.at_traverse(self.char1, self.room2)

        self.assertEqual(self.char1.location, self.room1)
        self.char1.msg.assert_called_once_with(
            "You need to stand before you can do that."
        )

    def test_generated_exit_allows_standing_traversal(self):
        self.call(ExitCommand(), "", obj=self.exit)

        self.assertEqual(self.char1.location, self.room2)


class TestPositionPresentation(EvenniaCommandTest):
    """Perception and room display consume the canonical effective position."""

    def test_sleeping_character_does_not_receive_external_messages(self):
        self.char1.db.position = "sleeping"

        received = self.char1.at_msg_receive("noise", from_obj=self.char2)

        self.assertFalse(received)

    def test_room_display_uses_effective_position(self):
        with patch.object(
            Character,
            "get_imposed_action_positions",
            return_value=[Position.FIGHTING],
        ):
            display = self.room1.get_display_characters(self.char1)

        self.assertIn("is fighting here", display)

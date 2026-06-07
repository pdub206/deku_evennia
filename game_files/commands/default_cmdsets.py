"""
Command sets

All commands in the game must be grouped in a cmdset.  A given command
can be part of any number of cmdsets and cmdsets can be added/removed
and merged onto entities at runtime.

To create new commands to populate the cmdset, see
`commands/command.py`.

This module wraps the default command sets of Evennia; overloads them
to add/remove commands from the default lineup. You can create your
own cmdsets by inheriting from them or directly from `evennia.CmdSet`.

"""

from commands.building import CmdAreas, CmdBuild, CmdLoadArea, CmdRooms
from commands.change import CmdChange
from commands.command import CmdNoInput
from commands.communication import CmdSay
from commands.generic import CmdInventory, CmdLook, CmdPose
from commands.position import CmdRest, CmdSit, CmdSleep, CmdStand, CmdWake
from commands.sheet import CmdSheet
from commands.skills import CmdSkills
from evennia import default_cmds
from evennia.contrib.rpg.character_creator.character_creator import \
    ContribChargenCmdSet


class CharacterCmdSet(default_cmds.CharacterCmdSet):
    """
    The `CharacterCmdSet` contains general in-game commands like `look`,
    `get`, etc available on in-game Character objects. It is merged with
    the `AccountCmdSet` when an Account puppets a Character.
    """

    key = "DefaultCharacter"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        # General commands - overrides Evennia's defaults.
        self.add(CmdLook)
        self.add(CmdSay)
        self.add(CmdPose)
        self.add(CmdInventory)
        # Position system.
        self.add(CmdSit)
        self.add(CmdRest)
        self.add(CmdSleep)
        self.add(CmdStand)
        self.add(CmdWake)
        # Character sheet commands.
        self.add(CmdSheet)
        self.add(CmdSkills)
        self.add(CmdChange)
        # Redraw a sticky prompt (e.g. the build editor's) on a bare Enter,
        # which otherwise runs no command and so wouldn't refresh it.
        self.add(CmdNoInput)
        # Builder tools (lock-gated to Builder perm). The sticky edit-mode
        # verbs live in BuildModeCmdSet, added to the caller while editing.
        self.add(CmdBuild)
        self.add(CmdAreas)
        self.add(CmdRooms)
        self.add(CmdLoadArea)


class AccountCmdSet(default_cmds.AccountCmdSet):
    """
    This is the cmdset available to the Account at all times. It is
    combined with the `CharacterCmdSet` when the Account puppets a
    Character. It holds game-account-specific commands, channel
    commands, etc.
    """

    key = "DefaultAccount"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        # Replace default charcreate/ic with the EvMenu-driven versions.
        self.add(ContribChargenCmdSet)


class UnloggedinCmdSet(default_cmds.UnloggedinCmdSet):
    """
    Command set available to the Session before being logged in.  This
    holds commands like creating a new account, logging in, etc.
    """

    key = "DefaultUnloggedin"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        #
        # any commands you add below will overload the default ones.
        #


class SessionCmdSet(default_cmds.SessionCmdSet):
    """
    This cmdset is made available on Session level once logged in. It
    is empty by default.
    """

    key = "DefaultSession"

    def at_cmdset_creation(self):
        """
        This is the only method defined in a cmdset, called during
        its creation. It should populate the set with command instances.

        As and example we just add the empty base `Command` object.
        It prints some info.
        """
        super().at_cmdset_creation()
        #
        # any commands you add below will overload the default ones.
        #

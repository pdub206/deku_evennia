"""Account-command wrappers that identify character lifecycle transitions."""

from evennia.commands.default.account import CmdOOC as _BaseCmdOOC
from evennia.contrib.rpg.character_creator.character_creator import \
    ContribCmdCharCreate as _BaseCmdCharCreate
from systems.lifecycle import (UnavailabilityCause,
                               clear_session_unpuppet_cause,
                               mark_session_unpuppet_cause)


class CmdOOC(_BaseCmdOOC):
    """Leave a character deliberately and return to the account screen.

    Usage:
      ooc
      unpuppet

    The wrapper preserves Evennia's normal OOC behavior while distinguishing a
    deliberate departure from a lost network connection for WORLD-03 cleanup.
    """

    def func(self) -> None:
        """Mark the Session only for the duration of the unpuppet operation."""
        session = self.session
        mark_session_unpuppet_cause(session, UnavailabilityCause.OOC)
        try:
            super().func()
        finally:
            clear_session_unpuppet_cause(session)


class CmdCharCreate(_BaseCmdCharCreate):
    """Start or resume creation of the account's one character."""

    def func(self) -> None:
        """Hide the creation flow once the persistent slot is occupied."""
        characters = list(self.account.characters)
        in_progress = [char for char in characters if char.db.chargen_step]
        if characters and not in_progress:
            self.msg(self.account.character_limit_message)
            return
        super().func()

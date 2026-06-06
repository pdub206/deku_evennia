"""
Characters

Characters are (by default) Objects setup to be puppeted by Accounts.
They are what you "see" in game. The Character class in this module
is setup to be the "default" character type created by the default
creation commands.

"""

import time

from evennia.objects.objects import DefaultCharacter

from .objects import ObjectParent


class Character(ObjectParent, DefaultCharacter):
    """
    The Character just re-implements some of the Object's methods and hooks
    to represent a Character entity in-game.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Object child classes like this.

    """

    def at_post_puppet(self, **kwargs) -> None:
        super().at_post_puppet(**kwargs)
        # Record the moment this session began so we can accumulate IC time.
        self.db.session_login_time = time.time()

    def at_post_unpuppet(self, account, session=None, **kwargs) -> None:
        # Accumulate elapsed IC time before releasing the character.
        login_time = self.db.session_login_time
        if login_time:
            elapsed = time.time() - login_time
            self.db.time_played = (self.db.time_played or 0.0) + elapsed
        self.db.session_login_time = None
        super().at_post_unpuppet(account, session=session, **kwargs)

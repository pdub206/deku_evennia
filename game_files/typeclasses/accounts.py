"""
Account

The Account represents the game "account" and each login has only one
Account object. An Account is what chats on default channels but has no
other in-game-world existence. Rather the Account puppets Objects (such
as Characters) in order to actually participate in the game world.


Guest

Guest accounts are simple low-level accounts that are created/deleted
on the fly and allows users to test the game without the commitment
of a full registration. Guest accounts are deactivated by default; to
activate them, add the following line to your settings file:

    GUEST_ENABLED = True

You will also need to modify the connection screen to reflect the
possibility to connect with a guest account. The setting file accepts
several more options for customizing the Guest account system.

"""

from typing import Any

from django.db import transaction
from evennia.accounts.accounts import CharactersHandler, DefaultGuest
from evennia.accounts.models import AccountDB
from evennia.contrib.rpg.character_creator.character_creator import \
    ContribChargenAccount
from evennia.objects.models import ObjectDB
from evennia.utils.utils import lazy_property


class Account(ContribChargenAccount):
    """
    An Account is the actual OOC player entity. It doesn't exist in the game,
    but puppets characters.

    This is the base Typeclass for all Accounts. Accounts represent
    the person playing the game and tracks account info, password
    etc. They are OOC entities without presence in-game. An Account
    can connect to a Character Object in order to "enter" the
    game.

    Account Typeclass API:

    * Available properties (only available on initiated typeclass objects)

     - key (string) - name of account
     - name (string)- wrapper for user.username
     - aliases (list of strings) - aliases to the object. Will be saved to
            database as AliasDB entries but returned as strings.
     - dbref (int, read-only) - unique #id-number. Also "id" can be used.
     - date_created (string) - time stamp of object creation
     - permissions (list of strings) - list of permission strings
     - user (User, read-only) - django User authorization object
     - obj (Object) - game object controlled by account. 'character' can also
                     be used.
     - is_superuser (bool, read-only) - if the connected user is a superuser

    * Handlers

     - locks - lock-handler: use locks.add() to add new lock strings
     - db - attribute-handler: store/retrieve database attributes on this
                              self.db.myattr=val, val=self.db.myattr
     - ndb - non-persistent attribute handler: same as db but does not
                                  create a database entry when storing data
     - scripts - script-handler. Add new scripts to object with scripts.add()
     - cmdset - cmdset-handler. Use cmdset.add() to add new cmdsets to object
     - nicks - nick-handler. New nicks with nicks.add().
     - sessions - session-handler. Use session.get() to see all sessions connected, if any
     - options - option-handler. Defaults are taken from settings.OPTIONS_ACCOUNT_DEFAULT
     - characters - handler for listing the account's playable characters

    * Helper methods (check autodocs for full updated listing)

     - msg(text=None, from_obj=None, session=None, options=None, **kwargs)
     - execute_cmd(raw_string)
     - search(searchdata, return_puppet=False, search_object=False, typeclass=None,
                      nofound_string=None, multimatch_string=None, use_nicks=True,
                      quiet=False, **kwargs)
     - is_typeclass(typeclass, exact=False)
     - swap_typeclass(new_typeclass, clean_attributes=False, no_default=True)
     - access(accessing_obj, access_type='read', default=False, no_superuser_bypass=False, **kwargs)
     - check_permstring(permstring)
     - get_cmdsets(caller, current, **kwargs)
     - get_cmdset_providers()
     - uses_screenreader(session=None)
     - get_display_name(looker, **kwargs)
     - get_extra_display_name_info(looker, **kwargs)
     - disconnect_session_from_account()
     - puppet_object(session, obj)
     - unpuppet_object(session)
     - unpuppet_all()
     - get_puppet(session)
     - get_all_puppets()
     - is_banned(**kwargs)
     - get_username_validators(validator_config=settings.AUTH_USERNAME_VALIDATORS)
     - authenticate(username, password, ip="", **kwargs)
     - normalize_username(username)
     - validate_username(username)
     - validate_password(password, account=None)
     - set_password(password, **kwargs)
     - get_character_slots()
     - get_available_character_slots()
     - create_character(*args, **kwargs)
     - create(*args, **kwargs)
     - delete(*args, **kwargs)
     - channel_msg(message, channel, senders=None, **kwargs)
     - idle_time()
     - connection_time()

    * Hook methods

     basetype_setup()
     at_account_creation()

     > note that the following hooks are also found on Objects and are
       usually handled on the character level:

     - at_init()
     - at_first_save()
     - at_access()
     - at_cmdset_get(**kwargs)
     - at_password_change(**kwargs)
     - at_first_login()
     - at_pre_login()
     - at_post_login(session=None)
     - at_failed_login(session, **kwargs)
     - at_disconnect(reason=None, **kwargs)
     - at_post_disconnect(**kwargs)
     - at_message_receive()
     - at_message_send()
     - at_server_reload()
     - at_server_shutdown()
     - at_look(target=None, session=None, **kwargs)
     - at_post_create_character(character, **kwargs)
     - at_post_add_character(char)
     - at_post_remove_character(char)
     - at_pre_channel_msg(message, channel, senders=None, **kwargs)
     - at_post_chnnel_msg(message, channel, senders=None, **kwargs)

    """

    character_limit_message = "Each account may own exactly one character."
    puppet_busy_message = "That character is already controlled by another session."

    @lazy_property
    def characters(self) -> "SingleCharacterHandler":
        """Return the ownership handler that enforces WORLD-04 on direct adds."""
        return SingleCharacterHandler(self)

    def create_character(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Any | None, list[str] | None]:
        """Create the account's sole PC while serializing competing requests."""
        if kwargs.pop("world04_admin_override", False):
            if not self.check_permstring("Developer"):
                return None, ["Developer permission is required for that operation."]
            self.ndb._world04_ownership_override = True
            try:
                return super().create_character(*args, **kwargs)
            finally:
                self.ndb._world04_ownership_override = False

        with transaction.atomic():
            locked = AccountDB.objects.select_for_update().get(pk=self.pk)
            if len(locked.characters) != 0:
                return None, [self.character_limit_message]
            return super(Account, locked).create_character(*args, **kwargs)

    def at_look(self, target: Any = None, session: Any = None, **kwargs: Any) -> str:
        """Show character creation only while the account has no owned PC."""
        appearance = super().at_look(target=target, session=session, **kwargs)
        if self.characters:
            appearance = appearance.replace(
                "  |wcharcreate|n - create new character\n", ""
            )
        return appearance

    def puppet_object(self, session: Any, obj: Any) -> None:
        """Puppet only the sole owned PC and never displace its controller."""
        return self._puppet_object_exclusive(session, obj, administrative=False)

    def puppet_object_for_admin(self, session: Any, obj: Any) -> None:
        """Use the explicit Developer-gated path for non-PC repair puppeting."""
        if not self.check_permstring("Developer"):
            raise RuntimeError("Developer permission is required for that operation.")
        return self._puppet_object_exclusive(session, obj, administrative=True)

    def _puppet_object_exclusive(
        self, session: Any, obj: Any, *, administrative: bool
    ) -> None:
        """Validate ownership and live control before Evennia mutates a Session."""
        if not session or not obj:
            return super().puppet_object(session, obj)

        with transaction.atomic():
            AccountDB.objects.select_for_update().get(pk=self.pk)
            ObjectDB.objects.select_for_update().get(pk=obj.pk)

            owned = list(self.characters)
            if not administrative and (len(owned) != 1 or owned[0] != obj):
                raise RuntimeError(
                    "This account's character ownership is invalid; contact staff."
                )

            if self.get_puppet(session) == obj:
                return None

            controllers = [
                controlling_session
                for controlling_session in obj.sessions.all()
                if controlling_session is not session
            ]
            if controllers:
                raise RuntimeError(self.puppet_busy_message)

            # Portal restoration can repopulate the character's SessionHandler
            # before rebuilding the Session's convenience references.
            if session in obj.sessions.all():
                session.puid = obj.id
                session.puppet = obj
                return None

            return super().puppet_object(session, obj)


class SingleCharacterHandler(CharactersHandler):
    """Fail closed when ordinary code tries to attach a second owned PC."""

    def add(self, character: Any) -> None:
        """Attach one character, rejecting malformed or duplicate ownership."""
        if self.owner.ndb._world04_ownership_override:
            super().add(character)
            return

        with transaction.atomic():
            locked = AccountDB.objects.select_for_update().get(pk=self.owner.pk)
            current = list(locked.characters)
            if character in current:
                return
            if current:
                raise RuntimeError(self.owner.character_limit_message)
            CharactersHandler.add(locked.characters, character)


class Guest(DefaultGuest):
    """
    This class is used for guest logins. Unlike Accounts, Guests and their
    characters are deleted after disconnection.
    """

    pass

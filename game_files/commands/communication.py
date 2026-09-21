"""
Communication commands: say, and future verbal commands (whisper, shout, etc.).

Speech is routed per-listener based on language knowledge.  Garbled output is
produced for listeners who don't understand the speaker's active language.
Sign language is handled separately from spoken language.
"""

from typing import Any, Sequence

from commands.command import Command, MuxCommand
from evennia.accounts.models import AccountDB
from evennia.comms.models import Msg
from evennia.utils import logger
from evennia.utils.eveditor import EvEditor
from systems.action_policy import ActionCategory
from systems.channels import released_channels
from systems.communication import authorize, ignored_by, normalize_text
from systems.doors import traversal_decision
from systems.language import garble, hand_pronoun, is_sign_language
from systems.mail import MAX_SUBJECT_LENGTH
from systems.mail import delete as delete_mail
from systems.mail import mail_messages
from systems.mail import read as read_mail
from systems.mail import received as received_mail
from systems.mail import reply_target
from systems.mail import send as send_mail
from systems.mail import sent as sent_mail
from systems.mail import snapshots as mail_snapshots
from systems.mail import unread_count
from systems.visibility import target_visibility


def _language(speaker: Any) -> tuple[str, bool]:
    """Return the speaker's valid active language and whether it is signed."""
    known: list[str] = speaker.db.languages or ["Common"]
    active = speaker.db.active_language
    if not active or active not in known:
        active = known[0]
        speaker.db.active_language = active
    return active, is_sign_language(active)


def _heard(
    speaker: Any,
    listener: Any,
    speech: str,
    *,
    verb: str,
    directed: Any | None = None,
) -> str:
    """Render speech once for a single listener in their known language."""
    active, sign = _language(speaker)
    label = active.lower()
    knows = active in (listener.db.languages or ["Common"])
    name = speaker.get_display_name(listener)
    if directed is not None:
        verb = f"{verb} {directed.get_display_name(listener)}"
    if sign and not knows:
        return f"{name} uses {hand_pronoun(speaker.db.gender or '')} hands to communicate in sign language."
    if sign or knows:
        return f'{name} {verb}, in {label},\n  "{speech}"'
    return f'{name} {verb}, in an unknown language,\n  "{garble(speech)}"'


def send_speech(
    speaker: Any,
    speech: str,
    *,
    recipients: Sequence[Any] | None = None,
    verb: str = "says",
    self_verb: str = "say",
    directed: Any | None = None,
) -> None:
    """Use the normal display- and language-aware spoken-message path.

    MOB-06 scripted speakers call this rather than constructing a separate
    dialogue channel.  ``recipients`` can narrow delivery for a directed reply.
    """
    active, _ = _language(speaker)
    lang_label = active.lower()
    self_target = (
        f" {directed.get_display_name(speaker)}" if directed is not None else ""
    )
    speaker.msg(f'You {self_verb}{self_target}, in {lang_label},\n  "{speech}"')
    if not speaker.location:
        return
    audience = recipients
    if audience is None:
        audience = speaker.location.contents_get(content_type="character")
    for obj in audience:
        if obj is speaker or getattr(obj, "location", None) is not speaker.location:
            continue
        if not ignored_by(obj, speaker):
            obj.msg(_heard(speaker, obj, speech, verb=verb, directed=directed))


def _message_error(caller: Any, reason: str) -> None:
    """Give one safe, consistent denial for shared text/rate validation."""
    messages = {
        "empty": "Say what?",
        "unsafe_text": "Your message contains unsupported control or formatting characters.",
        "too_long": "Messages may be at most 500 characters.",
        "rate_limited": "You are speaking too quickly. Please wait a moment.",
    }
    caller.msg(messages.get(reason, "You cannot communicate that right now."))


def _visible_target(caller: Any, query: str) -> Any | None:
    """Resolve exactly one visible co-located character without disclosure."""
    target = caller.search(query, location=caller.location)
    if target is None:
        return None
    if target is caller or not target_visibility(caller, target).visible:
        caller.msg("You cannot find that person here.")
        return None
    return target


def _direct_parts(command: Command) -> tuple[Any | None, str]:
    """Parse the delimiter-free ``target message`` directed-speech surface."""
    parts = command.args.strip().split(maxsplit=1)
    if len(parts) != 2:
        command.caller.msg(f"Usage: {command.key} <character> <message>")
        return None, ""
    query, raw = parts
    return _visible_target(command.caller, query), raw


def _account_for_tell(caller: Any, query: str) -> Any | None:
    """Resolve one account without revealing why an unavailable target failed."""
    name = query.strip()
    if not name:
        return None
    matches = list(AccountDB.objects.filter(username__iexact=name)[:2])
    if len(matches) != 1:
        return None
    target = matches[0]
    if (
        target == caller
        or not target.sessions.count()
        or not target.access(caller, "msg")
        or ignored_by(target, caller)
    ):
        return None
    return target


class CmdTell(MuxCommand):
    """Send an immediate private message to an online account.

    Usage:
      tell <account> <message>
      tell/reply <message>

    Tells are delivered only while the recipient is online. They are not saved
    and have no history, list, or read commands.
    """

    key = "tell"
    aliases = ("page",)
    switch_options = ("reply",)
    locks = "cmd:not pperm(page_banned)"
    help_category = "Communication"
    account_caller = True

    def func(self) -> None:
        """Validate and send one transient tell to one active account."""
        caller = self.caller
        if self.switches and "reply" not in self.switches:
            caller.msg("Usage: tell <account> <message> or tell/reply <message>")
            return
        if "reply" in self.switches:
            target_id = getattr(caller.ndb, "last_tell_account_id", None)
            try:
                target = AccountDB.objects.get(pk=int(target_id))
            except (AccountDB.DoesNotExist, TypeError, ValueError):
                target = None
            if (
                target is None
                or not target.sessions.count()
                or not target.access(caller, "msg")
                or ignored_by(target, caller)
            ):
                caller.msg("That person is unavailable.")
                return
            raw = self.args
        else:
            parts = self.args.strip().split(maxsplit=1)
            if len(parts) != 2:
                caller.msg("Usage: tell <account> <message>")
                return
            target = _account_for_tell(caller, parts[0])
            if target is None:
                caller.msg("That person is unavailable.")
                return
            raw = parts[1]

        result = authorize(caller, raw)
        if not result.accepted:
            _message_error(caller, result.reason)
            return
        caller.msg(f'You tell {target.key}, "{result.text}"')
        target.msg(f'{caller.key} tells you, "{result.text}"')
        caller.ndb.last_tell_account_id = target.id
        target.ndb.last_tell_account_id = caller.id


def _mail_editor_load(caller: Any) -> str:
    """Always begin a new mail composition with an empty immutable body."""
    return ""


def _mail_editor_save(caller: Any, buffer: str) -> bool:
    """Let Evennia acknowledge editor saves; delivery waits until editor exit."""
    return True


def _mail_editor_quit(caller: Any) -> None:
    """Deliver the editor buffer exactly once when its compose session exits."""
    compose = getattr(caller.ndb, "mail_compose", None)
    editor = getattr(caller.ndb, "_eveditor", None)
    caller.ndb.mail_compose = None
    if not isinstance(compose, dict) or editor is None:
        return
    try:
        target = AccountDB.objects.get(pk=int(compose["target_id"]))
    except (AccountDB.DoesNotExist, KeyError, TypeError, ValueError):
        caller.msg("That person is unavailable.")
        return
    result = send_mail(caller, target, compose.get("subject"), editor._buffer)
    if result.accepted:
        caller.msg(f"Mail sent to {target.key}.")
    elif result.reason == "invalid_text":
        caller.msg("Mail needs a plain-text body of at most 4,000 characters.")
    else:
        caller.msg("That person is unavailable.")


class CmdMail(MuxCommand):
    """Read, compose, and remove persistent account mail.

    Usage:
      mail
      mail/read <number>
      mail/send <account> = <subject>
      mail/reply <number>
      mail/sent
      mail/delete <number>

    Send and reply open Evennia's text editor. Leaving that editor sends its
    current body; it is not possible to supply a body on the command line.
    """

    key = "mail"
    switch_options = ("read", "send", "reply", "sent", "delete", "moderate")
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True
    rows_per_page = 20

    def func(self) -> None:
        """Dispatch the intentionally small, account-scoped mail surface."""
        caller = self.caller
        if len(self.switches) > 1:
            caller.msg(
                "Usage: mail, mail/read, mail/send, mail/reply, mail/sent, or mail/delete."
            )
            return
        switch = self.switches[0] if self.switches else "inbox"
        if switch == "inbox":
            self._list(received_mail(caller), outgoing=False)
        elif switch == "sent":
            self._list(sent_mail(caller), outgoing=True)
        elif switch == "read":
            self._read()
        elif switch == "send":
            self._compose()
        elif switch == "reply":
            self._reply()
        elif switch == "delete":
            self._delete()
        elif switch == "moderate":
            self._moderate()

    def _list(self, messages: Any, *, outgoing: bool) -> None:
        """Render one fixed-size page without exposing deleted mail."""
        page = 1
        if self.args.strip():
            try:
                page = int(self.args.strip())
            except ValueError:
                page = 0
        if page < 1:
            self.caller.msg("Mail page numbers start at 1.")
            return
        start = (page - 1) * self.rows_per_page
        rows = list(messages[start : start + self.rows_per_page])
        if not rows:
            self.caller.msg("You have no mail on that page.")
            return
        heading = (
            "Sent mail" if outgoing else f"Inbox ({unread_count(self.caller)} unread)"
        )
        lines = [f"{heading}, page {page}:"]
        for message in rows:
            snapshot = mail_snapshots(message)
            name = snapshot.get(
                "recipient_name" if outgoing else "sender_name", "Unknown"
            )
            unread = (
                "*"
                if not outgoing and message.tags.has("unread", category="communication")
                else " "
            )
            lines.append(f"{unread}{message.id:>5} {name}: {message.header}")
        self.caller.msg("\n".join(lines))

    def _read(self) -> None:
        """Open exactly one received message, marking it read only on success."""
        result = read_mail(self.caller, self.args.strip())
        if not result.accepted:
            self.caller.msg("You have no such mail.")
            return
        message = result.message
        sender = mail_snapshots(message).get("sender_name", "Unknown")
        self.caller.msg(
            f"Mail #{message.id} from {sender}: {message.header}\n\n{message.message}"
        )

    def _compose(self) -> None:
        """Resolve recipient and subject before entering the body editor."""
        if not self.lhs.strip() or not self.rhs.strip() or "|" in self.args:
            self.caller.msg("Usage: mail/send <account> = <subject>")
            return
        subject = self.rhs.strip()
        if len(subject) > MAX_SUBJECT_LENGTH:
            self.caller.msg("Mail subjects may be at most 80 characters.")
            return
        target = _account_for_tell(self.caller, self.lhs)
        if target is None:
            # Mail may be delivered offline; only account existence, locks, and
            # ignore are relevant here, unlike tells.
            matches = list(
                AccountDB.objects.filter(username__iexact=self.lhs.strip())[:2]
            )
            target = matches[0] if len(matches) == 1 else None
            if (
                target is None
                or target == self.caller
                or not target.access(self.caller, "msg")
                or ignored_by(target, self.caller)
            ):
                self.caller.msg("That person is unavailable.")
                return
        self._open_editor(target, subject)

    def _reply(self) -> None:
        """Reply through the editor to the original sender of received mail."""
        target = reply_target(self.caller, self.args.strip())
        if target is None or target == self.caller:
            self.caller.msg("You have no such mail.")
            return
        try:
            original = received_mail(self.caller).get(pk=int(self.args.strip()))
        except (Msg.DoesNotExist, TypeError, ValueError):
            self.caller.msg("You have no such mail.")
            return
        self._open_editor(target, f"Re: {original.header}"[:MAX_SUBJECT_LENGTH])

    def _open_editor(self, target: Any, subject: str) -> None:
        """Persist only the transient compose context while the editor is live."""
        self.caller.ndb.mail_compose = {"target_id": target.id, "subject": subject}
        EvEditor(
            self.caller,
            loadfunc=_mail_editor_load,
            savefunc=_mail_editor_save,
            quitfunc=_mail_editor_quit,
            key="mail composition",
            persistent=False,
        )

    def _delete(self) -> None:
        """Hide this caller's view, never the other account's view."""
        result = delete_mail(self.caller, self.args.strip())
        self.caller.msg(
            "Mail deleted." if result.accepted else "You have no such mail."
        )

    def _moderate(self) -> None:
        """Allow only Admins to remove a message with a server-audited reason."""
        if (
            not self.caller.check_permstring("Admin")
            or not self.lhs.strip()
            or not self.rhs.strip()
        ):
            self.caller.msg("Usage: mail/moderate <number> = <reason>")
            return
        try:
            message = mail_messages().get(pk=int(self.lhs.strip()))
        except (Msg.DoesNotExist, ValueError):
            self.caller.msg("No such mail exists.")
            return
        logger.log_info(
            f"COMM-03A mail moderation: admin=#{self.caller.id} mail=#{message.id} reason={self.rhs.strip()[:500]!r}"
        )
        message.delete()
        self.caller.msg("Mail removed and audited.")


class CmdIgnore(MuxCommand):
    """Manage the accounts whose optional communication you do not receive.

    Usage:
      ignore
      ignore/add <account>
      ignore/remove <account>
    """

    key = "ignore"
    switch_options = ("add", "remove")
    locks = "cmd:all()"
    help_category = "Communication"
    account_caller = True
    maximum_ignored_accounts = 100

    def func(self) -> None:
        """Persist a bounded, de-duplicated account-id ignore list."""
        caller = self.caller
        raw_ids = getattr(caller.db, "ignored_account_ids", ()) or ()
        try:
            ignored_ids = {int(value) for value in raw_ids}
        except (TypeError, ValueError):
            ignored_ids = set()
        if not self.switches:
            if not ignored_ids:
                caller.msg("You are not ignoring anyone.")
                return
            names = list(
                AccountDB.objects.filter(id__in=ignored_ids).values_list(
                    "username", flat=True
                )
            )
            caller.msg("Ignored accounts: " + ", ".join(sorted(names, key=str.lower)))
            return
        if len(self.switches) != 1 or not self.args.strip():
            caller.msg("Usage: ignore/add <account> or ignore/remove <account>")
            return
        matches = list(AccountDB.objects.filter(username__iexact=self.args.strip())[:2])
        if len(matches) != 1 or matches[0] == caller:
            caller.msg("That account is unavailable.")
            return
        target = matches[0]
        if "add" in self.switches:
            if target.id in ignored_ids:
                caller.msg(f"You are already ignoring {target.key}.")
                return
            if len(ignored_ids) >= self.maximum_ignored_accounts:
                caller.msg("You cannot ignore more than 100 accounts.")
                return
            caller.db.ignored_account_ids = sorted((*ignored_ids, target.id))
            caller.msg(f"You now ignore {target.key}.")
            return
        if "remove" in self.switches:
            if target.id not in ignored_ids:
                caller.msg(f"You are not ignoring {target.key}.")
                return
            caller.db.ignored_account_ids = sorted(ignored_ids - {target.id})
            caller.msg(f"You no longer ignore {target.key}.")
            return
        caller.msg("Usage: ignore/add <account> or ignore/remove <account>")


class CmdChannel(MuxCommand):
    """Use the OOC and Newbie channels, or manage your subscriptions.

    Usage:
      channel
      channel <OOC|Newbie> = <message>
      channel/sub, /unsub, /mute, /unmute <OOC|Newbie>
      channel/alias <OOC|Newbie> = <alias>
      channel/unalias <alias>
      channel/history <OOC|Newbie>
      channel/who <OOC|Newbie>
      channel/purge <OOC|Newbie>       (Admin)
    """

    key = "channel"
    aliases = ("@channel", "@chan", "@channels")
    switch_options = (
        "sub",
        "unsub",
        "mute",
        "unmute",
        "alias",
        "unalias",
        "history",
        "who",
        "purge",
        "create",
        "destroy",
        "rename",
        "lock",
        "boot",
        "ban",
        "unban",
    )
    locks = "cmd:not pperm(channel_banned)"
    help_category = "Communication"
    account_caller = True

    def _channel(self, name: str) -> Any | None:
        """Resolve only one of the fixed released channels and its aliases."""
        query = name.strip().lower()
        matches = [
            channel
            for channel in released_channels()
            if query == channel.key.lower() or query in channel.aliases.all()
        ]
        if len(matches) != 1:
            self.caller.msg("No released channel matches that name.")
            return None
        return matches[0]

    def _list(self) -> None:
        """Show fixed channels and the caller's subscription/mute state."""
        lines = ["Released channels:"]
        for channel in released_channels():
            if not channel.has_connection(self.caller):
                state = "not subscribed"
            elif self.caller in channel.mutelist:
                state = "muted"
            else:
                state = "subscribed"
            lines.append(f"  {channel.key}: {state} — {channel.db.desc}")
        self.caller.msg("\n".join(lines))

    def _history(self, channel: Any) -> None:
        """Show the retained plain-text history to current subscribers only."""
        if not channel.has_connection(self.caller):
            self.caller.msg(f"You are not subscribed to {channel.key}.")
            return
        history = list(channel.db.comm_history or [])
        if not history:
            self.caller.msg(f"There is no retained history for {channel.key}.")
            return
        self.caller.msg(
            "\n".join(
                f"[{channel.key}] {entry['sender']}: {entry['text']}"
                for entry in history
                if isinstance(entry, dict)
                and isinstance(entry.get("sender"), str)
                and isinstance(entry.get("text"), str)
            )
        )

    def func(self) -> None:
        """Keep channel use constrained to the released player-facing surface."""
        switches = set(self.switches)
        if switches & {"create", "destroy", "rename", "lock", "boot", "ban", "unban"}:
            self.caller.msg("Channel administration is restricted to staff tools.")
            return
        if not self.args.strip() and not switches:
            self._list()
            return
        if "unalias" in switches:
            alias = self.args.strip()
            if not alias:
                self.caller.msg("Usage: channel/unalias <alias>")
                return
            for channel in released_channels():
                if self.caller.nicks.has(alias, category="channel"):
                    channel.remove_user_channel_alias(self.caller, alias)
                    self.caller.msg(f"Removed your channel alias '{alias}'.")
                    return
            self.caller.msg("No such channel alias was defined.")
            return
        name = self.lhs.strip()
        channel = self._channel(name)
        if channel is None:
            return
        if "purge" in switches:
            if not self.caller.check_permstring("Admin"):
                self.caller.msg("You are not permitted to purge channel history.")
                return
            removed = len(channel.db.comm_history or [])
            channel.db.comm_history = []
            logger.log_sec(
                f"COMM-01C channel history purged: channel={channel.key} "
                f"count={removed} actor={self.caller.key}"
            )
            self.caller.msg(f"Purged {removed} retained messages from {channel.key}.")
            return
        if "sub" in switches:
            if channel.has_connection(self.caller):
                self.caller.msg(f"You are already subscribed to {channel.key}.")
            elif channel.connect(self.caller):
                self.caller.msg(f"You are now subscribed to {channel.key}.")
            else:
                self.caller.msg(f"You cannot subscribe to {channel.key}.")
            return
        if "unsub" in switches:
            if channel.has_connection(self.caller) and channel.disconnect(self.caller):
                self.caller.msg(f"You unsubscribed from {channel.key}.")
            else:
                self.caller.msg(f"You are not subscribed to {channel.key}.")
            return
        if "mute" in switches:
            self.caller.msg(
                f"Muted channel {channel.key}."
                if channel.mute(self.caller)
                else f"Channel {channel.key} is already muted."
            )
            return
        if "unmute" in switches:
            self.caller.msg(
                f"Un-muted channel {channel.key}."
                if channel.unmute(self.caller)
                else f"Channel {channel.key} is not muted."
            )
            return
        if "alias" in switches:
            alias = self.rhs.strip()
            if not alias or not channel.has_connection(self.caller):
                self.caller.msg(
                    "Usage: channel/alias <channel> = <alias> (while subscribed)"
                )
                return
            channel.add_user_channel_alias(self.caller, alias.lower())
            self.caller.msg(
                f"Added/updated your alias '{alias}' for channel {channel.key}."
            )
            return
        if "history" in switches:
            self._history(channel)
            return
        if "who" in switches:
            names = [account.key for account in channel.subscriptions.online()]
            self.caller.msg(
                f"Subscribed to {channel.key}: " + (", ".join(names) or "<None>")
            )
            return
        if switches:
            self.caller.msg(
                "Usage: channel[/sub|unsub|mute|unmute|alias|unalias|history|who] ..."
            )
            return
        if not channel.has_connection(self.caller):
            self.caller.msg(f"You are not subscribed to {channel.key}.")
            return
        result = authorize(self.caller, self.rhs)
        if not result.accepted:
            _message_error(self.caller, result.reason)
            return
        if not channel.access(self.caller, "send"):
            self.caller.msg(f"You are not allowed to send messages to {channel.key}.")
            return
        channel.msg(result.text, senders=self.caller)


class CmdAnnounce(MuxCommand):
    """Send an immediate, non-ignorable Admin announcement. Usage: announce <message>"""

    key = "announce"
    locks = "cmd:perm(Admin)"
    help_category = "Staff"
    account_caller = True

    def func(self) -> None:
        """Deliver a single clearly-prefixed broadcast without channel history."""
        result = normalize_text(self.args)
        if not result.accepted:
            _message_error(self.caller, result.reason)
            return
        recipients = [
            account for account in AccountDB.objects.all() if account.sessions.count()
        ]
        text = f"|r[ANNOUNCEMENT]|n {result.text}"
        for account in recipients:
            account.msg(text)
        logger.log_sec(
            f"COMM-01C announcement: actor={self.caller.key} recipients={len(recipients)}"
        )


class CmdSay(Command):
    """
    Say something aloud in your active language.

    Usage:
      say <message>
      "<message>
      '<message>

    Other characters who know your active language hear you clearly.
    Those who don't hear garbled speech instead.  Use |wchange language|n
    to switch which language you speak.
    """

    action_category = ActionCategory.COMMUNICATE
    key = "say"
    aliases = ('"', "'")
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        caller = self.caller
        result = authorize(caller, self.args)
        if not result.accepted:
            _message_error(caller, result.reason)
            return
        speech = result.text

        send_speech(caller, speech)
        if caller.location:
            # isort: off
            from systems.mobile_specials import (
                SpecialEvent,
                dispatch_room_specials,
            )

            # isort: on

            dispatch_room_specials(
                caller.location,
                SpecialEvent(
                    "speech",
                    actor=caller,
                    text=speech,
                    language=caller.db.active_language,
                ),
            )


class CmdWhisper(Command):
    """Whisper privately when the shared action policy allows communication.

    Usage:
      whisper <character> <message>
    """

    action_category = ActionCategory.COMMUNICATE
    key = "whisper"
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Deliver language-aware private speech only to a visible target."""
        target, raw = _direct_parts(self)
        if target is None:
            return
        result = authorize(self.caller, raw)
        if not result.accepted:
            _message_error(self.caller, result.reason)
            return
        if ignored_by(target, self.caller):
            self.caller.msg("That person is unavailable.")
            return
        active, _sign = _language(self.caller)
        self.caller.msg(
            f'You whisper to {target.get_display_name(self.caller)}, in {active.lower()},\n  "{result.text}"'
        )
        target.msg(_heard(self.caller, target, result.text, verb="whispers"))


class CmdAsk(Command):
    """Ask one visible local character a question others can hear.

    Usage: ask <character> <message>
    """

    key = "ask"
    locks = "cmd:all()"
    help_category = "Communication"
    action_category = ActionCategory.COMMUNICATE

    def func(self) -> None:
        """Broadcast an audible directed question using listener language rules."""
        target, raw = _direct_parts(self)
        if target is None:
            return
        result = authorize(self.caller, raw)
        if not result.accepted:
            _message_error(self.caller, result.reason)
            return
        if ignored_by(target, self.caller):
            self.caller.msg("That person is unavailable.")
            return
        send_speech(
            self.caller,
            result.text,
            verb="asks",
            self_verb="ask",
            directed=target,
        )
        if self.caller.location:
            # isort: off
            from systems.mobile_specials import (
                SpecialEvent,
                dispatch_room_specials,
            )

            # isort: on

            dispatch_room_specials(
                self.caller.location,
                SpecialEvent(
                    "speech",
                    actor=self.caller,
                    text=result.text,
                    language=self.caller.db.active_language,
                ),
            )


class CmdShout(Command):
    """Shout into this room and each room one open exit away. Usage: shout <message>"""

    key = "shout"
    locks = "cmd:all()"
    help_category = "Communication"
    action_category = ActionCategory.COMMUNICATE

    def func(self) -> None:
        """Deliver one-hop nonrecursive speech; signed language cannot carry."""
        active, sign = _language(self.caller)
        if sign:
            self.caller.msg("You cannot shout in a signed language.")
            return
        result = authorize(self.caller, self.args, cost=3)
        if not result.accepted:
            _message_error(self.caller, result.reason)
            return
        room = self.caller.location
        if room is None:
            return
        rooms = {room}
        for exit_obj in room.exits:
            if (
                getattr(exit_obj, "destination", None) is not None
                and traversal_decision(exit_obj).allowed
            ):
                rooms.add(exit_obj.destination)
        self.caller.msg(f'You shout, in {active.lower()},\n  "{result.text}"')
        delivered: set[int] = set()
        for destination in rooms:
            for listener in destination.contents_get(content_type="character"):
                if (
                    listener is self.caller
                    or listener.id in delivered
                    or ignored_by(listener, self.caller)
                ):
                    continue
                delivered.add(listener.id)
                listener.msg(_heard(self.caller, listener, result.text, verb="shouts"))

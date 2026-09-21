"""
Communication commands: say, and future verbal commands (whisper, shout, etc.).

Speech is routed per-listener based on language knowledge.  Garbled output is
produced for listeners who don't understand the speaker's active language.
Sign language is handled separately from spoken language.
"""

from typing import Any, Sequence

from commands.command import Command, MuxCommand
from evennia.accounts.models import AccountDB
from systems.action_policy import ActionCategory
from systems.communication import authorize, ignored_by
from systems.doors import traversal_decision
from systems.language import garble, hand_pronoun, is_sign_language
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

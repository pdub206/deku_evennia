"""
Communication commands: say, and future verbal commands (whisper, shout, etc.).

Speech is routed per-listener based on language knowledge.  Garbled output is
produced for listeners who don't understand the speaker's active language.
Sign language is handled separately from spoken language.
"""

from typing import Any, Sequence

from evennia.commands.default.general import CmdSay as _BaseSay
from evennia.commands.default.general import CmdWhisper as _BaseWhisper
from systems.action_policy import ActionCategory
from systems.language import garble, hand_pronoun, is_sign_language


def send_speech(
    speaker: Any, speech: str, *, recipients: Sequence[Any] | None = None
) -> None:
    """Use the normal display- and language-aware spoken-message path.

    MOB-06 scripted speakers call this rather than constructing a separate
    dialogue channel.  ``recipients`` can narrow delivery for a directed reply.
    """
    known: list[str] = speaker.db.languages or ["Common"]
    active = speaker.db.active_language
    if not active or active not in known:
        active = known[0]
        speaker.db.active_language = active
    lang_label = active.lower()
    sign = is_sign_language(active)
    speaker.msg(f'You say, in {lang_label},\n  "{speech}"')
    if not speaker.location:
        return
    audience = recipients
    if audience is None:
        audience = speaker.location.contents_get(content_type="character")
    for obj in audience:
        if obj is speaker or getattr(obj, "location", None) is not speaker.location:
            continue
        listener_langs: list[str] = obj.db.languages or ["Common"]
        knows = active in listener_langs
        name = speaker.get_display_name(obj)
        if sign:
            if knows:
                obj.msg(f'{name} says, in {lang_label},\n  "{speech}"')
            else:
                obj.msg(
                    f"{name} uses {hand_pronoun(speaker.db.gender or '')} hands to communicate in sign language."
                )
        elif knows:
            obj.msg(f'{name} says, in {lang_label},\n  "{speech}"')
        else:
            obj.msg(f'{name} says, in an unknown language,\n  "{garble(speech)}"')


class CmdSay(_BaseSay):
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

    def func(self) -> None:
        caller = self.caller
        if not self.args:
            caller.msg("Say what?")
            return

        speech = self.args.strip()

        send_speech(caller, speech)
        if caller.location:
            from systems.mobile_specials import (SpecialEvent,
                                                 dispatch_room_specials)

            dispatch_room_specials(
                caller.location,
                SpecialEvent(
                    "speech",
                    actor=caller,
                    text=speech,
                    language=caller.db.active_language,
                ),
            )


class CmdWhisper(_BaseWhisper):
    """Whisper privately when the shared action policy allows communication.

    Usage:
      whisper <character> = <message>
    """

    action_category = ActionCategory.COMMUNICATE

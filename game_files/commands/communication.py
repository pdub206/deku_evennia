"""
Communication commands: say, and future verbal commands (whisper, shout, etc.).

Speech is routed per-listener based on language knowledge.  Garbled output is
produced for listeners who don't understand the speaker's active language.
Sign language is handled separately from spoken language.
"""

from evennia.commands.default.general import CmdSay as _BaseSay

from commands.position import _ASLEEP_MSG, _is_sleeping
from systems.language import garble, hand_pronoun, is_sign_language


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

    def func(self) -> None:
        if _is_sleeping(self.caller):
            self.caller.msg(_ASLEEP_MSG)
            return

        caller = self.caller
        if not self.args:
            caller.msg("Say what?")
            return

        speech = self.args.strip()

        # Resolve active language, initialising from first known if unset.
        known: list[str] = caller.db.languages or ["Common"]
        active = caller.db.active_language
        if not active or active not in known:
            active = known[0]
            caller.db.active_language = active

        lang_label = active.lower()
        sign = is_sign_language(active)

        # The speaking character always sees their own speech clearly.
        caller.msg(f'You say, in {lang_label},\n  "{speech}"')

        if not caller.location:
            return

        for obj in caller.location.contents_get(content_type="character"):
            if obj is caller:
                continue

            listener_langs: list[str] = obj.db.languages or ["Common"]
            knows = active in listener_langs
            name = caller.get_display_name(obj)

            if sign:
                if knows:
                    obj.msg(f'{name} says, in {lang_label},\n  "{speech}"')
                else:
                    pronoun = hand_pronoun(caller.db.gender or "")
                    obj.msg(
                        f"{name} uses {pronoun} hands to communicate in sign language."
                    )
            else:
                if knows:
                    obj.msg(f'{name} says, in {lang_label},\n  "{speech}"')
                else:
                    obj.msg(
                        f'{name} says, in an unknown language,\n  "{garble(speech)}"'
                    )

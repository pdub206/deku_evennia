"""
The `change` command and its sub-commands.

Usage:
  change language <name>

Sub-commands are dispatched by the first word of the argument so that new
options (e.g. `change title`, `change prompt`) can be added here without
touching the command key or help text structure.
"""

from commands.command import Command


class CmdChange(Command):
    """
    Change a character setting.

    Usage:
      change language <language>

    Examples:
      change language common
      change language draconic

    See |whelp change language|n for details on the language sub-command.
    """

    key = "change"
    help_category = "Character"

    def func(self) -> None:
        args = self.args.strip()
        if not args:
            self.caller.msg("Usage: change language <language>")
            return

        parts = args.split(None, 1)
        subcmd = parts[0].lower()
        remainder = parts[1] if len(parts) > 1 else ""

        if subcmd == "language":
            self._change_language(remainder.strip())
        else:
            self.caller.msg(
                f"Unknown option '{subcmd}'. Usage: change language <language>"
            )

    def _change_language(self, lang_arg: str) -> None:
        if not lang_arg:
            char = self.caller
            current = char.db.active_language or "Common"
            known = char.db.languages or ["Common"]
            self.caller.msg(
                f"Current language: |w{current}|n\n"
                f"Known languages: {', '.join(known)}\n"
                f"Usage: change language <language>"
            )
            return

        char = self.caller
        known: list[str] = char.db.languages or ["Common"]

        # Case-insensitive match against known languages.
        match = next((lang for lang in known if lang.lower() == lang_arg.lower()), None)

        if match is None:
            char.msg("You don't know that language.")
            return

        current = char.db.active_language or "Common"
        if match == current:
            char.msg(f"You are already speaking {match}.")
            return

        char.db.active_language = match
        char.msg(f"You switch to speaking |w{match}|n.")

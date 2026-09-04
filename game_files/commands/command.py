"""
Commands

Commands describe the input the account can do to the game.

"""

from evennia.commands.cmdhandler import CMD_NOINPUT
from evennia.commands.command import Command as BaseCommand
from evennia.commands.default.muxcommand import MuxCommand as BaseMuxCommand
from systems.action_policy import ActionCategory


class _CommandHooksMixin:
    """Apply declared action policy and preserve persistent input prompts.

    DEKU has no prompt by default — input happens on a bare line.  A feature
    that wants a sticky prompt (currently the build editor) sets
    ``caller.ndb._prompt`` to the prompt string and clears it when finished.
    Terminals don't keep a prompt around once new output arrives, so this hook
    re-emits it after *every* command the caller runs — look, movement, say, a
    build verb, anything — keeping it pinned to the input line for the whole
    session.  When ``_prompt`` is unset (the normal case) this is a no-op.

    These hooks live on a mixin so both command bases below share them: the project
    ``Command`` (custom commands) and ``MuxCommand`` (wired in via
    ``settings.COMMAND_DEFAULT_CLASS`` so Evennia's own default and exit
    commands honour it too).
    """

    action_category: ActionCategory | None = None

    def at_pre_cmd(self) -> bool:
        """Abort a declared in-character action when the policy denies it."""
        if super().at_pre_cmd():
            return True
        if self.action_category is None:
            return False
        policy = getattr(self.caller, "actions", None)
        if policy is None:
            return False
        decision = policy.check(self.action_category)
        if decision.allowed:
            return False
        self.caller.msg(decision.message)
        return True

    def at_post_cmd(self):
        super().at_post_cmd()
        caller = self.caller
        ndb = getattr(caller, "ndb", None)
        prompt = ndb._prompt if ndb is not None else None
        if prompt:
            caller.msg(prompt=prompt)


class Command(_CommandHooksMixin, BaseCommand):
    """
    Base command (you may see this if a child command had no help text defined)

    Note that the class's `__doc__` string is used by Evennia to create the
    automatic help entry for the command, so make sure to document consistently
    here. Without setting one, the parent's docstring will show (like now).

    """

    # Each Command class implements the following methods, called in this order
    # (only func() is actually required):
    #
    #     - at_pre_cmd(): If this returns anything truthy, execution is aborted.
    #     - parse(): Should perform any extra parsing needed on self.args
    #         and store the result on self.
    #     - func(): Performs the actual work.
    #     - at_post_cmd(): Extra actions, often things done after
    #         every command, like prompts.
    #

    # Most project commands manipulate the in-character world. Commands in a
    # different family declare that family on their class.
    action_category = ActionCategory.MANIPULATE


class MuxCommand(_CommandHooksMixin, BaseMuxCommand):
    """Project default command class, wired in via `COMMAND_DEFAULT_CLASS`.

    Evennia supplies both in-character and account/staff commands through this
    base, so it has no policy category by default. Project wrappers declare a
    category for the in-character defaults that need one.
    """


class CmdNoInput(BaseCommand):
    """Handle empty input (a bare Enter) by redrawing the persistent prompt.

    Empty input is routed by Evennia to the special `CMD_NOINPUT` command rather
    than a normal one, so it bypasses `_CommandHooksMixin.at_post_cmd`. Without
    this, pressing Enter while editing would leave you with no prompt until your
    next real command.  When no sticky prompt is set (`ndb._prompt` is unset)
    this does nothing — preserving Evennia's default "Enter does nothing".

    It subclasses the plain base command (not the project `Command`) so a bare
    Enter never triggers action policy or other per-command side effects.
    """

    key = CMD_NOINPUT
    locks = "cmd:all()"

    def func(self):
        ndb = getattr(self.caller, "ndb", None)
        prompt = ndb._prompt if ndb is not None else None
        if prompt:
            self.caller.msg(prompt=prompt)

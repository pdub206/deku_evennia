"""Registry-backed fixed, nonverbal social commands for COMM-02."""

from __future__ import annotations

from typing import Any

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.communication import authorize, ignored_by
from systems.socials import SOCIALS, render, social_for
from systems.visibility import target_visibility


def _target(caller: Any, query: str) -> Any | None:
    """Resolve a self or one visible character in the caller's current room."""
    if not caller.location:
        caller.msg("You cannot find that person here.")
        return None
    target = caller.search(query, location=caller.location)
    if target is None:
        return None
    if target is not caller and not target_visibility(caller, target).visible:
        caller.msg("You cannot find that person here.")
        return None
    if target is not caller and not target.is_typeclass(
        "typeclasses.characters.Character", exact=False
    ):
        caller.msg("You can only direct a social at a character.")
        return None
    return target


class CmdSocial(Command):
    """Perform one released nonverbal social.

    Usage:
      <social> [visible local character]

    The released socials are fixed expressions. They have no language or game
    mechanic effects and are subject to the normal communication rate limit.
    """

    action_category = ActionCategory.COMMUNICATE
    key = "bow"
    aliases = tuple(key for key in SOCIALS if key != "bow")
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Authorize and deliver one fixed social to its exact local audience."""
        definition = social_for(self.cmdstring)
        if definition is None:
            self.caller.msg("That social is unavailable.")
            return
        caller = self.caller
        query = self.args.strip()
        target = _target(caller, query) if query else None
        if query and target is None:
            return
        if target is not None and target is not caller and ignored_by(target, caller):
            caller.msg("That person is unavailable.")
            return
        result = authorize(caller, definition.key)
        if not result.accepted:
            caller.msg("You are communicating too quickly. Please wait a moment.")
            return

        templates = definition.templates
        if target is caller:
            caller.msg(render(templates.self_actor, caller, caller))
        elif target is not None:
            caller.msg(render(templates.target_actor, caller, caller, target))
            target.msg(render(templates.target_target, caller, target, target))
        else:
            caller.msg(render(templates.actor, caller, caller))

        if not caller.location:
            return
        for observer in caller.location.contents_get(content_type="character"):
            if observer is caller or observer is target or ignored_by(observer, caller):
                continue
            template = (
                templates.self_observer
                if target is caller
                else (
                    templates.target_observer
                    if target is not None
                    else templates.observer
                )
            )
            observer.msg(render(template, caller, observer, target))


class CmdSocials(Command):
    """List the fixed socials currently available. Usage: socials"""

    action_category = ActionCategory.COMMUNICATE
    key = "socials"
    locks = "cmd:all()"
    help_category = "Communication"

    def func(self) -> None:
        """Show the immutable registry order without exposing implementation data."""
        if self.args.strip():
            self.caller.msg("Usage: socials")
            return
        self.caller.msg("Available socials: " + ", ".join(SOCIALS) + ".")

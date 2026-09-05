"""Player entry point for joining and retargeting basic combat."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.attacks import can_attack
from systems.combat import get_target, schedule_tactical_action, start_fight
from systems.equipment import HIT_LOCATIONS


class CmdAttack(Command):
    """Begin attacking a character; later pulse rounds attack automatically.

    Usage:
      attack <target>
      kill <target>
      hit <target>

    Attacking starts or joins a fight but never grants an immediate attack.
    """

    key = "attack"
    aliases = ("kill", "hit")
    help_category = "Combat"
    action_category = ActionCategory.COMBAT

    def func(self) -> None:
        """Validate one visible room target and update combat intent safely."""
        if not self.args.strip():
            self.caller.msg("Attack whom?")
            return
        query = self.args.strip()
        own_names = {
            self.caller.key.casefold(),
            *(alias.casefold() for alias in self.caller.aliases.all()),
        }
        target = (
            self.caller
            if query.casefold() in own_names
            else self.caller.search(query, location=self.caller.location)
        )
        if target is None:
            return
        decision = can_attack(self.caller, target)
        if not decision.allowed:
            self.caller.msg(_attack_denial_message(decision.reason))
            return
        current_target = get_target(self.caller)
        result = start_fight(self.caller, target)
        if not result.accepted:
            self.caller.msg(_attack_denial_message(result.reason))
            return
        if not result.changed and current_target is target:
            self.caller.msg(
                f"You are already fighting {target.get_display_name(self.caller)}."
            )
            return
        self.caller.msg(f"You begin fighting {target.get_display_name(self.caller)}.")


def _attack_denial_message(reason: str) -> str:
    """Map stable internal policy reasons to safe player-facing feedback."""
    messages = {
        "self": "You cannot attack yourself.",
        "not_character": "You can only attack another character.",
        "not_colocated": "Your target is not here.",
        "attacker_ineligible": "You cannot attack right now.",
        "target_ineligible": "That target cannot fight right now.",
        "target_defeated": "That target is already down.",
        "protected": "That target is protected from combat.",
        "staff_immune": "That target cannot be attacked.",
        "access_denied": "You cannot attack that target.",
        "pvp_denied": "Player-versus-player combat is not enabled here.",
    }
    return messages.get(reason, "You cannot attack that target.")


class _QueuedTacticalCommand(Command):
    """Shared parser and queue behavior for next-action tactical commands."""

    action_key = ""
    action_category = ActionCategory.COMBAT

    def _queue(self, target, **arguments: str) -> None:
        result = schedule_tactical_action(
            self.caller, self.action_key, target, **arguments
        )
        if not result.accepted:
            messages = {
                "not_fighting": "You can only do that while fighting.",
                "invalid_target": "That target is not in your fight.",
                "invalid_action": "That tactical action is unavailable.",
            }
            self.caller.msg(messages.get(result.reason, "You cannot do that now."))
            return
        name = target.get_display_name(self.caller)
        if result.changed:
            self.caller.msg(
                f"You prepare to {self.action_key} {name} on your next action."
            )
        else:
            self.caller.msg(f"You are already prepared to {self.action_key} {name}.")

    def _find_target(self, query: str):
        """Resolve a visible co-located target without accepting ambiguous text."""
        target = self.caller.search(query, location=self.caller.location)
        return target


class CmdAim(_QueuedTacticalCommand):
    """Queue a disadvantaged strike at one chosen location.

    Usage:
      aim <location>
      aim <target> <location>
    """

    key = "aim"
    action_key = "aim"
    help_category = "Combat"

    def func(self) -> None:
        """Parse an unambiguous current-target shorthand or explicit target."""
        words = self.args.strip().split()
        if not words:
            self.caller.msg("Aim where?")
            return
        target = None
        location = ""
        for split in range(0, len(words)):
            candidate = " ".join(words[split:]).casefold()
            if candidate in HIT_LOCATIONS:
                if split == 0:
                    target = get_target(self.caller)
                else:
                    target = self._find_target(" ".join(words[:split]))
                location = candidate
                break
        if location not in HIT_LOCATIONS:
            self.caller.msg("Choose a supported hit location.")
            return
        if target is None:
            self.caller.msg("Specify a combat target before choosing that location.")
            return
        self._queue(target, location=location)


class CmdBackstab(_QueuedTacticalCommand):
    """Queue a Rogue Sneak Attack attempt.

    Usage:
      backstab [target]
    """

    key = "backstab"
    action_key = "backstab"
    help_category = "Combat"

    def func(self) -> None:
        """Use the current target when no explicit target was supplied."""
        target = (
            get_target(self.caller)
            if not self.args.strip()
            else self._find_target(self.args.strip())
        )
        if target is None:
            self.caller.msg("Specify a combat target.")
            return
        self._queue(target)


class CmdBash(_QueuedTacticalCommand):
    """Queue a shield bash against one combat target.

    Usage:
      bash [target]
    """

    key = "bash"
    action_key = "bash"
    help_category = "Combat"

    def func(self) -> None:
        """Use the current target when no explicit target was supplied."""
        target = (
            get_target(self.caller)
            if not self.args.strip()
            else self._find_target(self.args.strip())
        )
        if target is None:
            self.caller.msg("Specify a combat target.")
            return
        self._queue(target)


class CmdKick(_QueuedTacticalCommand):
    """Queue a Strength-based kick against one combat target.

    Usage:
      kick [target]
    """

    key = "kick"
    action_key = "kick"
    help_category = "Combat"

    def func(self) -> None:
        """Use the current target when no explicit target was supplied."""
        target = (
            get_target(self.caller)
            if not self.args.strip()
            else self._find_target(self.args.strip())
        )
        if target is None:
            self.caller.msg("Specify a combat target.")
            return
        self._queue(target)

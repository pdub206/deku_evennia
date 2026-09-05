"""Player entry point for joining and retargeting basic combat."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.attacks import can_attack
from systems.combat import get_target, start_fight


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

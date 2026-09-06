"""Player command surface for MOB-07's controlled-pet command forwarding."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.mobile_relationships import dismiss, order, set_following, stay


class CmdOrder(Command):
    """Give a controlled pet one registered game action.

    Usage:
      order <pet> <action>

    Examples:
      order hound follow
      order hound stay
      order hound flee

    The pet must be in your room and currently controlled by you. Actions are
    a deliberately small allowlist: follow, stay, and flee. Orders never run
    arbitrary character commands, movement, speech, or staff tools.
    """

    key = "order"
    locks = "cmd:all()"
    help_category = "Character"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Parse a target and registered action without raw-command forwarding."""
        pet_name, separator, remainder = self.args.strip().partition(" ")
        action_key, separator, arguments = remainder.strip().partition(" ")
        if not separator and not action_key:
            self.msg("Usage: order <pet> <action>")
            return
        pet = self.caller.search(pet_name, location=self.caller.location)
        if not pet:
            return
        result = order(self.caller, pet, action_key, arguments if separator else "")
        self.msg(
            f"{pet.key} obeys."
            if result.status in {"accepted", "acted", "queued"}
            else "That order cannot be carried out."
        )


class CmdPet(Command):
    """Control a pet you own or currently charm.

    Usage:
      pet <pet> follow
      pet <pet> stay
      pet <pet> release

    ``follow`` and ``stay`` work for the effective controller; only the
    durable owner may ``release`` a pet. Acquiring and charm remain abilities
    or content events with the NPC's explicit control locks.
    """

    key = "pet"
    aliases = ("companion",)
    locks = "cmd:all()"
    help_category = "Character"
    action_category = ActionCategory.MANIPULATE

    def func(self) -> None:
        """Dispatch one player-safe direct pet-control action."""
        pet_name, separator, action = self.args.strip().partition(" ")
        if not separator or not pet_name or not action.strip():
            self.msg("Usage: pet <pet> follow|stay|release")
            return
        pet = self.caller.search(pet_name, location=self.caller.location)
        if not pet:
            return
        action = action.strip().casefold()
        if action == "follow":
            result = set_following(self.caller, pet)
        elif action == "stay":
            result = stay(self.caller, pet)
        elif action == "release":
            result = dismiss(self.caller, pet)
        else:
            self.msg("Usage: pet <pet> follow|stay|release")
            return
        self.msg(
            f"{pet.key} acknowledges you."
            if result.accepted
            else "That pet action cannot be carried out."
        )

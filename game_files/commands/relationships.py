"""Player command surface for MOB-07's controlled-pet command forwarding."""

from __future__ import annotations

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.mobile_relationships import order


class CmdOrder(Command):
    """Give a controlled pet one normal game command.

    Usage:
      order <pet> <command>

    Examples:
      order hound get all corpse
      order hound put gem bag

    The pet must be in your room and currently controlled by you. The command
    is run through the pet's normal command set, so its ordinary command locks
    and action rules still apply. A pet cannot issue another order.
    """

    key = "order"
    locks = "cmd:all()"
    help_category = "Character"
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Split target and command text, leaving command parsing to the pet."""
        pet_name, separator, command_text = self.args.strip().partition(" ")
        if not separator or not pet_name or not command_text.strip():
            self.msg("Usage: order <pet> <command>")
            return
        pet = self.caller.search(pet_name, location=self.caller.location)
        if not pet:
            return
        result = order(self.caller, pet, command_text)
        self.msg(
            f"{pet.key} obeys."
            if result.status == "acted"
            else "That order cannot be carried out."
        )

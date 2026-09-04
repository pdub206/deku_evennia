"""
Exits

Exits are connectors between Rooms. An exit always has a destination property
set and has a single command defined on itself with the same name as its key,
for allowing Characters to traverse the exit to its destination.

"""

from evennia.objects.objects import DefaultExit
from evennia.objects.objects import ExitCommand as DefaultExitCommand
from systems.action_policy import ActionCategory

from .objects import ObjectParent


class ExitCommand(DefaultExitCommand):
    """Generated traversal command governed by the shared movement policy."""

    action_category = ActionCategory.MOVE


class Exit(ObjectParent, DefaultExit):
    """
    Exits are connectors between rooms. Exits are normal Objects except
    they defines the `destination` property and overrides some hooks
    and methods to represent the exits.

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Objects child classes like this.

    """

    exit_command = ExitCommand

    def at_traverse(self, traversing_object, target_location, **kwargs) -> None:
        """Reject direct exit traversal once, before Evennia handles movement."""
        policy = getattr(traversing_object, "actions", None)
        if policy is not None:
            decision = policy.check(ActionCategory.MOVE)
            if not decision.allowed:
                traversing_object.msg(decision.message)
                return
        super().at_traverse(traversing_object, target_location, **kwargs)

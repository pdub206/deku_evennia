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
        """Reject unavailable doors before Evennia handles ordinary movement."""
        from systems.visibility import target_visibility

        if not target_visibility(traversing_object, self).visible:
            traversing_object.msg("You cannot go that way.")
            return False
        from systems.doors import traversal_decision

        door = traversal_decision(self)
        if not door.allowed:
            if not kwargs.get("mobile_navigation"):
                traversing_object.msg(door.message)
            return False
        if kwargs.get("combat_flee"):
            from systems.combat_movement import combat_flee_active

            token = getattr(traversing_object.ndb, "_combat_flee_token", None)
            if not combat_flee_active(traversing_object, token):
                return False
            return traversing_object.move_to(
                target_location,
                move_type="combat_flee",
                combat_flee_token=token,
            )
        policy = getattr(traversing_object, "actions", None)
        if policy is not None:
            decision = policy.check(ActionCategory.MOVE)
            if not decision.allowed:
                traversing_object.msg(decision.message)
                return
        if kwargs.get("mobile_navigation"):
            # MOB-04 already selected a legal exit, but movement still uses the
            # ordinary traversal mode so action, encumbrance, and room hooks
            # receive exactly the same contract as player travel.
            return traversing_object.move_to(
                target_location,
                move_type="traverse",
                use_destination=False,
                travel_authorized=True,
            )
        if kwargs.get("travel_execution"):
            return traversing_object.move_to(
                target_location, move_type="traverse", travel_authorized=True
            )
        from systems.travel import denial_message, schedule_travel

        result = schedule_travel(traversing_object, self)
        if result.status.value in {"queued", "replaced"}:
            traversing_object.msg(f"You begin traveling {self.key}.")
        else:
            traversing_object.msg(denial_message(result.reason))
        return result.status.value in {"queued", "replaced"}

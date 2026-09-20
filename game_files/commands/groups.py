"""Player command surface for GROUP-02A party management."""

from commands.command import Command
from systems.action_policy import ActionCategory
from systems import groups


class CmdGroup(Command):
    """Create and manage a durable party.

    Usage: group [invite|accept|decline|leave|kick|leader|disband] [character]
    """

    key = "group"
    locks = "cmd:all()"
    help_category = "Character"
    # The service itself blocks membership changes in combat, while a leadership
    # transfer is deliberately legal because it does not alter a combat roster.
    action_category = ActionCategory.STATE_INDEPENDENT

    def func(self) -> None:
        """Dispatch the intentionally small party-management grammar."""
        verb, _, name = self.args.strip().partition(" ")
        verb = verb.casefold()
        if not verb:
            group = groups.group_for(self.caller)
            if group is None:
                self.msg("You are not in a group.")
            else:
                names = [
                    getattr(groups._pc_by_id(member), "key", "someone")
                    for member in group["members"]
                ]
                self.msg(
                    f"Leader: {getattr(groups._pc_by_id(group['leader_id']), 'key', 'someone')}\nMembers: {', '.join(names)}"
                )
            return
        if verb in {"leave", "disband"} and name:
            self.msg("Usage: group " + verb)
            return
        if verb not in {"leave", "disband"} and not name:
            self.msg("Usage: group " + verb + " <character>")
            return
        target = (
            self.caller.search(name, location=self.caller.location) if name else None
        )
        if name and not target:
            return
        operation = {
            "invite": lambda: groups.invite(self.caller, target),
            "accept": lambda: groups.accept(self.caller, target),
            "decline": lambda: groups.decline(self.caller, target),
            "leave": lambda: groups.leave(self.caller),
            "kick": lambda: groups.kick(self.caller, target),
            "leader": lambda: groups.transfer_leader(self.caller, target),
            "disband": lambda: groups.disband(self.caller),
        }.get(verb)
        if operation is None:
            self.msg(
                "Usage: group [invite|accept|decline|leave|kick|leader|disband] [character]"
            )
            return
        result = operation()
        messages = {
            "invited": f"You invite {target.key}.",
            "already_invited": f"{target.key} already has your invitation.",
            "joined": f"You join {target.key}'s group.",
            "declined": "You decline the invitation.",
            "left": "You leave the group.",
            "transferred": f"{target.key} is now the group leader.",
            "already_leader": "That member is already the leader.",
            "disbanded": "You disband the group.",
        }
        self.msg(messages.get(result.reason, "That group action cannot be completed."))
        if result.accepted and result.reason == "invited":
            target.msg(
                f"{self.caller.key} invites you to a group. Type group accept {self.caller.key} or group decline {self.caller.key}."
            )

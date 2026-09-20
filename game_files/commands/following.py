"""Player commands for GROUP-01A's consensual PC following."""

from commands.command import Command
from systems.player_following import accept, decline, request, unlink
from systems.visibility import target_visibility


class CmdFollow(Command):
    """Ask another visible PC to let you follow them, or answer an ask.

    Usage:
      follow <character>
      follow/accept <character>
      follow/decline <character>
    """

    key = "follow"
    switches = ("accept", "decline")
    locks = "cmd:all()"
    help_category = "Character"

    def func(self) -> None:
        """Resolve a same-room PC then dispatch the narrow graph operation."""
        name = self.args.strip()
        if not name:
            self.msg("Usage: follow <character> or follow/accept|decline <character>")
            return
        target = self.caller.search(name, location=self.caller.location)
        if not target:
            return
        if not target_visibility(self.caller, target).visible:
            self.msg("You cannot see that person.")
            return
        if "accept" in self.switches:
            result = accept(self.caller, target)
            if result.accepted:
                self.msg(f"You allow {target.key} to follow you.")
                target.msg(f"{self.caller.key} allows you to follow them.")
            else:
                self.msg("That follow request cannot be accepted.")
        elif "decline" in self.switches:
            result = decline(self.caller, target)
            self.msg(
                f"You decline {target.key}'s follow request."
                if result.accepted
                else "There is no such follow request."
            )
            if result.accepted:
                target.msg(f"{self.caller.key} declines your follow request.")
        else:
            result = request(self.caller, target)
            if result.accepted:
                self.msg(f"You ask to follow {target.key}.")
                if result.reason == "requested":
                    target.msg(
                        f"{self.caller.key} asks to follow you. Type follow/accept {self.caller.key} or follow/decline {self.caller.key}."
                    )
            else:
                self.msg("You cannot follow that person.")


class CmdUnfollow(Command):
    """Stop following, or remove one direct follower.

    Usage:
      unfollow
      unfollow <character>
    """

    key = "unfollow"
    locks = "cmd:all()"
    help_category = "Character"

    def func(self) -> None:
        """Unlink without requiring either party's consent."""
        name = self.args.strip()
        target = (
            self.caller.search(name, location=self.caller.location) if name else None
        )
        if name and not target:
            return
        result = unlink(self.caller, target)
        if result.accepted:
            self.msg(
                "You stop following."
                if target is None
                else f"You stop {target.key} from following you."
            )
            if target is not None and result.reason == "unlinked":
                target.msg(f"{self.caller.key} stops you from following them.")
        else:
            self.msg("That person is not directly following you.")

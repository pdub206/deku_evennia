"""Player commands for GROUP-01A's consensual PC following."""

from commands.command import Command, MuxCommand
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
    switch_options = ("accept", "decline")
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


class CmdFollowAdmin(MuxCommand):
    """Inspect or explicitly repair a PC follow record.

    Usage:
      @follow [<character or #dbref>]
      @follow/repair [<character or #dbref>]

    This is intentionally a Builder-only recovery surface. Normal follow
    commands never repair malformed durable state implicitly.
    """

    key = "@follow"
    locks = "cmd:perm(Builder)"
    help_category = "Staff"
    switch_options = ("repair",)

    def func(self) -> None:
        """Show primitive graph state or perform one explicit repair."""
        from systems.player_following import inspect, repair

        if self.switches and any(switch != "repair" for switch in self.switches):
            self.msg("Usage: @follow[/repair] [<character or #dbref>]")
            return
        target = (
            self.caller.search(self.args.strip(), global_search=True)
            if self.args.strip()
            else self.caller
        )
        if target is None:
            return
        if not getattr(getattr(target, "db", None), "is_player_character", False):
            self.msg("That object has no PC follow state.")
            return
        if "repair" in self.switches:
            changed = repair(target)
            self.msg(
                f"Repaired {target.key}'s follow state: {changed} record(s) changed."
            )
            return
        try:
            state = inspect(target)
        except ValueError:
            self.msg(f"{target.key}'s follow state is invalid; use @follow/repair.")
            return
        leader = state["state"]["leader_id"]
        request = state["state"]["request_id"]
        members = (
            ", ".join(str(identifier) for identifier in state["members"]) or "none"
        )
        self.msg(
            f"{target.key}: leader #{leader or 'none'}; request #{request or 'none'}; "
            f"connected PCs: {members}."
        )

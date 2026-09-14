"""Player commands for INTERACT-01B door and container manipulation."""

from __future__ import annotations

from typing import Any

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.door_actions import DoorActionError, manipulate_target
from systems.doors import DoorError, door_state


def _find_target(caller: Any, query: str) -> Any | None:
    """Resolve visible local exits/containers without leaking excluded matches."""
    if not query:
        caller.msg("Manipulate what?")
        return None
    candidates = list(caller.location.contents) + list(caller.contents)
    matches = caller.search(query, candidates=candidates, quiet=True, use_locks=False)
    usable = []
    for target in matches:
        try:
            state = door_state(target)
        except DoorError:
            state = None
        if (
            state is not None
            and not state.hidden
            and target.access(caller, "interact", default=True)
        ):
            usable.append(target)
    if len(usable) != 1:
        caller.msg("You do not see one clear target by that name.")
        return None
    return usable[0]


def _room_message(caller: Any, target: Any, verb: str) -> None:
    """Send one receiver-specific local message for a logical transition."""
    for receiver in caller.location.contents:
        if receiver == caller:
            continue
        actor_name = caller.get_display_name(receiver)
        target_name = target.get_display_name(receiver)
        receiver.msg(f"{actor_name} {verb} {target_name}.")


class DoorCommand(Command):
    """Base for one-word door and container manipulation commands."""

    action_category = ActionCategory.MANIPULATE
    locks = "cmd:all()"

    def func(self) -> None:
        target = _find_target(self.caller, self.args.strip())
        if target is None:
            return
        try:
            result = manipulate_target(self.caller, target, self.key)
        except DoorActionError as exc:
            self.caller.msg(str(exc))
            return
        name = target.get_display_name(self.caller)
        if self.key == "pick" and not result.changed:
            self.caller.msg(f"You fail to pick {name}.")
            _room_message(self.caller, target, "tries to pick")
            return
        verb = "unlock" if self.key == "pick" else self.key
        self.caller.msg(f"You {verb} {name}.")
        room_verb = {
            "open": "opens",
            "close": "closes",
            "lock": "locks",
            "unlock": "unlocks",
        }[verb]
        _room_message(self.caller, target, room_verb)


class CmdOpen(DoorCommand):
    """Open a visible, unlocked door or container. Usage: open <target>"""

    key = "open"


class CmdClose(DoorCommand):
    """Close a visible open door or container. Usage: close <target>"""

    key = "close"


class CmdLock(DoorCommand):
    """Lock a closed target with a directly carried matching key."""

    key = "lock"


class CmdUnlock(DoorCommand):
    """Unlock a target with a directly carried matching key."""

    key = "unlock"


class CmdPick(DoorCommand):
    """Pick a lock with directly carried thieves' tools. Usage: pick <target>"""

    key = "pick"

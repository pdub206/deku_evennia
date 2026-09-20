"""Player manipulation commands for finite lamps and mundane refill vessels."""

from typing import Any

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.item_resources import ItemResourceError, refill_resource, set_light


def _carried_item(actor: Any, name: str) -> Any | None:
    """Resolve one directly carried viewable item without exposing hidden names."""
    if not name or len(name) > 128:
        actor.msg("Name one directly carried item.")
        return None
    candidates = [
        item
        for item in actor.contents
        if item.access(actor, "view", default=True)
        and item.access(actor, "interact", default=True)
    ]
    matches = actor.search(name, candidates=candidates, quiet=True, use_locks=False)
    if len(matches) != 1:
        actor.msg("Name one directly carried item.")
        return None
    return matches[0]


class _LightCommand(Command):
    """Share carried-item resolution and the authoritative light transition."""

    action_category = ActionCategory.MANIPULATE
    locks = "cmd:all()"
    help_category = "General"

    def func(self) -> None:
        """Resolve a lamp and let the service own validation and committed output."""
        item = _carried_item(self.caller, self.args.strip())
        if item is None:
            return
        try:
            set_light(self.caller, item, self.key == "light")
        except ItemResourceError as err:
            self.msg(str(err))


class CmdLight(_LightCommand):
    """Light a fuelled lamp carried directly by you. Usage: light <item>"""

    key = "light"


class CmdExtinguish(_LightCommand):
    """Extinguish a carried lamp and stop its fuel use. Usage: extinguish <item>"""

    key = "extinguish"


class CmdRefill(Command):
    """Transfer compatible liquid/fuel units. Usage: refill <target> from <source>"""

    key = "refill"
    action_category = ActionCategory.MANIPULATE
    locks = "cmd:all()"
    help_category = "General"

    def func(self) -> None:
        """Require two directly carried compatible vessels and explicit grammar."""
        if len(self.args) > 300 or " from " not in self.args.casefold():
            self.msg("Usage: refill <target> from <source>.")
            return
        separator = self.args.casefold().index(" from ")
        target = _carried_item(self.caller, self.args[:separator].strip())
        if target is None:
            return
        source = _carried_item(self.caller, self.args[separator + 6 :].strip())
        if source is None:
            return
        try:
            refill_resource(self.caller, target, source)
        except ItemResourceError as err:
            self.msg(str(err))

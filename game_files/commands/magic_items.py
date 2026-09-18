"""ITEM-04B player commands for potions, scrolls, wands, and staves.

Each command only names one carried item and an optional target; every access,
reservation, and magic decision belongs to ``systems.magic_items``.
"""

from __future__ import annotations

from typing import Any

from commands.command import Command
from systems.action_policy import ActionCategory
from systems.item_resources import ItemResourceError
from systems.magic_actions import MagicActionError
from systems.magic_items import MagicItemError, activate_magic_item
from systems.magic_resources import MagicResourceError

_MAX_ARGUMENT_LENGTH = 300


def _carried_item(actor: Any, name: str) -> Any | None:
    """Resolve one directly carried viewable item without leaking hidden names."""
    if not name or len(name) > 128:
        actor.msg("Name one magic item you are carrying.")
        return None
    candidates = [
        item
        for item in actor.contents
        if item.access(actor, "view", default=True)
        and item.access(actor, "interact", default=True)
    ]
    matches = actor.search(name, candidates=candidates, quiet=True, use_locks=False)
    if len(matches) != 1:
        actor.msg("Name one magic item you are carrying.")
        return None
    return matches[0]


class _ActivationCommand(Command):
    """Share the one activation grammar and the single service entry point."""

    action_category = ActionCategory.MANIPULATE
    locks = "cmd:all()"
    help_category = "Magic"
    usage = ""

    def func(self) -> None:
        """Parse `<item> [on <target>]` and let the service own every denial."""
        parsed = _parse_activation(self.args)
        if parsed is None:
            self.msg(f"Usage: {self.usage}")
            return
        item_name, target_name = parsed
        item = _carried_item(self.caller, item_name)
        if item is None:
            return
        try:
            activate_magic_item(
                self.caller,
                item,
                command=self.key,
                target_name=target_name,
            )
        except (
            MagicItemError,
            MagicActionError,
            MagicResourceError,
            ItemResourceError,
        ) as err:
            self.msg(str(err))


class CmdQuaff(_ActivationCommand):
    """Drink one portion of a carried potion.

    Usage:
      quaff <potion>
      quaff <potion> on <target>

    A potion affects you unless its magic lets you name someone else. Its last
    portion empties the bottle, which then disappears.
    """

    key = "quaff"
    usage = "quaff <potion> [on <target>]"


class CmdRecite(_ActivationCommand):
    """Read a carried scroll aloud, using it up.

    Usage:
      recite <scroll>
      recite <scroll> on <target>

    Some scrolls can only be read by someone who already knows their spell.
    """

    key = "recite"
    usage = "recite <scroll> [on <target>]"


class CmdUseDevice(_ActivationCommand):
    """Spend one charge from a carried wand or staff.

    Usage:
      use <item>
      use <item> on <target>

    A device with no charges left cannot be invoked until it recharges.
    """

    key = "use"
    usage = "use <item> [on <target>]"


def _parse_activation(raw: str) -> tuple[str, str | None] | None:
    """Split only the final `` on `` marker so multiword item names survive.

    ``value`` is already stripped, so a matched marker always has text on both
    sides; an item whose own name ends in "on" keeps its name instead.
    """
    value = raw.strip()
    if not value or len(value) > _MAX_ARGUMENT_LENGTH:
        return None
    item, marker, target = value.rpartition(" on ")
    return (item.strip(), target.strip()) if marker else (value, None)

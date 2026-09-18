"""Builder inspection and audited repair for ITEM-05A transfer and decay state."""

from typing import Any

from commands.command import MuxCommand
from systems.item_decay import (
    DECAY_ATTRIBUTE,
    DECAY_POLICY_ATTRIBUTE,
    QUARANTINE_CATEGORY,
    QUARANTINE_TAG,
    ItemDecayError,
    repair_decay,
)
from systems.item_transfer import (
    AUDIT_ATTRIBUTE,
    BINDING_ATTRIBUTE,
    TransferPolicyError,
    staff_move,
    staff_unbind,
)

_USAGE = (
    "Usage: itempolicy <item> | itempolicy/move <item> = <destination>, <reason> | "
    "itempolicy/unbind <item> = <reason> | itempolicy/decay <item> = <reason>"
)


class CmdItemPolicy(MuxCommand):
    """
    Inspect or repair an item's transfer flags, binding, and decay timer.

    Usage:
      itempolicy <item>
      itempolicy/move <item> = <destination>, <reason>
      itempolicy/unbind <item> = <reason>
      itempolicy/decay <item> = <reason>

    Every repair requires a reason and is recorded on the item and in the log.
    """

    key = "itempolicy"
    locks = "cmd:perm(Builder)"
    help_category = "Builder"

    def func(self) -> None:
        """Dispatch the single requested inspection or repair."""
        operation = next(iter(self.switches), "inspect").casefold()
        if operation not in {"inspect", "move", "unbind", "decay"} or not self.lhs:
            self.msg(_USAGE)
            return
        item = self.caller.search(self.lhs.strip(), global_search=True)
        if not item:
            return
        if not item.is_typeclass("typeclasses.objects.Item"):
            self.msg("Only items have transfer and decay policy.")
            return
        if operation == "inspect":
            self.msg(self._render(item))
            return
        try:
            self._repair(operation, item)
        except (TransferPolicyError, ItemDecayError) as err:
            self.msg(str(err))

    def _repair(self, operation: str, item: Any) -> None:
        """Run one audited repair with a mandatory reason."""
        rhs = (self.rhs or "").strip()
        if operation == "move":
            destination_name, _, reason = rhs.partition(",")
            if not destination_name.strip() or not reason.strip():
                self.msg(_USAGE)
                return
            destination = self.caller.search(
                destination_name.strip(), global_search=True
            )
            if not destination:
                return
            staff_move(item, destination, self.caller, reason)
            self.msg(f"Moved {item.key} (#{item.id}) to {destination.key}.")
            return
        if not rhs:
            self.msg(_USAGE)
            return
        if operation == "unbind":
            staff_unbind(item, self.caller, rhs)
            self.msg(f"Unbound {item.key} (#{item.id}).")
        else:
            repair_decay(item, self.caller, rhs)
            self.msg(f"Reset the decay timer on {item.key} (#{item.id}).")

    @staticmethod
    def _render(item: Any) -> str:
        """Show raw primitive policy state, including malformed values."""
        audit = item.attributes.get(AUDIT_ATTRIBUTE) or []
        quarantined = item.tags.has(QUARANTINE_TAG, category=QUARANTINE_CATEGORY)
        lines = [
            f"|w{item.key}|n (#{item.id}) in "
            f"{getattr(item.location, 'key', 'nowhere')}",
            f"  no_drop: {item.attributes.get('no_drop')!r}",
            f"  account_bound: {item.attributes.get('account_bound')!r}",
            f"  binding: {item.attributes.get(BINDING_ATTRIBUTE)!r}",
            f"  decay_minutes: {item.attributes.get(DECAY_POLICY_ATTRIBUTE)!r}",
            f"  decay record: {item.attributes.get(DECAY_ATTRIBUTE)!r}"
            + (" |r(quarantined)|n" if quarantined else ""),
            f"  audit entries: {len(audit)}",
        ]
        lines.extend(
            f"    {entry.get('time')} {entry.get('operation')} by "
            f"#{entry.get('actor')}: {entry.get('reason')}"
            for entry in list(audit)[-3:]
        )
        return "\n".join(lines)

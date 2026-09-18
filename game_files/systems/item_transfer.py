"""ITEM-05A canonical item transfer policy.

Two authored flags restrict where an Item may go:

* ``no_drop`` — a carried item cannot voluntarily leave its holder's possession
  (drop, give, put, junk, sale). COMBAT-05 death transfer still moves it.
* ``account_bound`` — the item records one immutable owning PC at its first
  grant and may then exist only inside that PC's carried container tree. It is
  retained across death, and may never be sold, given, dropped, junked, placed
  in a public corpse, rebound by players, or carried by NPCs.

Commands ask :func:`transfer_denial` for a player-safe message before moving
anything; :func:`move_denial` is the same possession rule applied by
``Item.at_pre_move`` so direct ``move_to`` calls cannot bypass it. Staff repair
passes an explicit, audited ``transfer_bypass`` reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from evennia.utils import logger

NO_DROP_ATTRIBUTE = "no_drop"
ACCOUNT_BOUND_ATTRIBUTE = "account_bound"
BINDING_ATTRIBUTE = "item_binding"
AUDIT_ATTRIBUTE = "item_policy_audit"
BINDING_VERSION = 1
MAX_AUDIT_ENTRIES = 20
MAX_REASON_LENGTH = 200
MAX_TREE_DEPTH = 64
OPERATIONS = frozenset({"get", "drop", "give", "put", "junk", "sell", "reset"})
# Moves that are never voluntary player transfers. Rollback restores the exact
# pre-command location; death transfer and decay spill are system-owned.
_SYSTEM_NO_DROP_KWARGS = ("corpse_transfer", "item_decay")


class TransferPolicyError(ValueError):
    """Malformed transfer data that needs staff repair."""


def _is_item(obj: Any) -> bool:
    """Only ordinary Items carry transfer flags."""
    return bool(obj is not None and obj.is_typeclass("typeclasses.objects.Item"))


def _is_character(obj: Any) -> bool:
    """Match PCs and NPCs without importing the typeclass at module load."""
    return bool(
        obj is not None
        and obj.is_typeclass("typeclasses.characters.Character", exact=False)
    )


def is_pc(character: Any) -> bool:
    """Match COMBAT-04's default-PC convention (unset means a PC)."""
    value = character.attributes.get("is_player_character")
    return True if value is None else bool(value)


def holder_of(obj: Any) -> Any | None:
    """Return the first Character at or above ``obj``, or None if uncarried."""
    current, depth = obj, 0
    while current is not None and depth <= MAX_TREE_DEPTH:
        if _is_character(current):
            return current
        current, depth = current.location, depth + 1
    return None


def _flag(item: Any, name: str) -> bool:
    """Read one strict boolean flag; anything else fails closed for staff repair."""
    value = item.attributes.get(name)
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise TransferPolicyError(f"Item #{item.id} has a malformed {name} flag.")


def transfer_flags(item: Any) -> tuple[bool, bool]:
    """Return validated ``(no_drop, account_bound)`` for one Item."""
    if not _is_item(item):
        return False, False
    return _flag(item, NO_DROP_ATTRIBUTE), _flag(item, ACCOUNT_BOUND_ATTRIBUTE)


def bound_owner_id(item: Any) -> int | None:
    """Return the recorded immutable owner, validating the versioned record."""
    raw = item.attributes.get(BINDING_ATTRIBUTE)
    if raw is None:
        return None
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "owner_id"}
        or raw["version"] != BINDING_VERSION
        or isinstance(raw["owner_id"], bool)
        or not isinstance(raw["owner_id"], int)
        or raw["owner_id"] < 1
    ):
        raise TransferPolicyError(f"Item #{item.id} has a malformed binding.")
    return raw["owner_id"]


def effective_owner_id(item: Any) -> int | None:
    """Recorded owner, or the PC holding a bound item that predates its grant hook.

    Objects created directly inside a character skip move hooks, so possession
    by a PC is treated as that first grant rather than leaving a loophole.
    """
    owner_id = bound_owner_id(item)
    if owner_id is not None:
        return owner_id
    holder = holder_of(item.location)
    return holder.id if holder is not None and is_pc(holder) else None


def bind_to_holder(item: Any) -> None:
    """Record the first PC grant for every bound item in ``item``'s subtree."""
    for obj in _subtree(item):
        try:
            if not transfer_flags(obj)[1] or bound_owner_id(obj) is not None:
                continue
        except TransferPolicyError:
            continue
        holder = holder_of(obj.location)
        if holder is not None and is_pc(holder):
            obj.attributes.add(
                BINDING_ATTRIBUTE, {"version": BINDING_VERSION, "owner_id": holder.id}
            )


def _subtree(item: Any) -> list[Any]:
    """Return ``item`` and its nested contents in stable breadth-first order."""
    found, pending, seen = [], [item], set()
    while pending and len(found) <= 10000:
        obj = pending.pop(0)
        if obj.id in seen:
            continue
        seen.add(obj.id)
        found.append(obj)
        pending.extend(sorted(obj.contents, key=lambda child: child.id))
    return found


def _name(item: Any, looker: Any | None) -> str:
    """Display name for player-safe messages."""
    return item.get_display_name(looker) if looker is not None else item.key


def move_denial(item: Any, destination: Any, **kwargs: Any) -> str | None:
    """Apply the possession rule to ``item`` and every nested item it carries.

    A carried ``no_drop`` item must keep its holder (and, when moved itself,
    may only go into the holder's direct inventory); a bound item must end up
    in its owner's carried tree, and an unbound one may never enter an NPC's.
    """
    bypass = kwargs.get("transfer_bypass")
    if bypass is not None:
        if not isinstance(bypass, str) or not bypass.strip():
            return "That transfer needs staff repair."
        logger.log_info(
            f"Item transfer bypass: #{item.id} -> "
            f"#{getattr(destination, 'id', None)} ({bypass.strip()[:MAX_REASON_LENGTH]})"
        )
        return None
    if kwargs.get("move_type") == "rollback":
        return None
    new_holder = holder_of(destination) if destination is not None else None
    system_move = any(kwargs.get(name) for name in _SYSTEM_NO_DROP_KWARGS)
    for obj in _subtree(item):
        try:
            no_drop, account_bound = transfer_flags(obj)
            if account_bound:
                owner_id = effective_owner_id(obj)
                if owner_id is None:
                    if new_holder is not None and not is_pc(new_holder):
                        return f"{_name(obj, None)} cannot be given to that."
                elif new_holder is None or new_holder.id != owner_id:
                    return f"{_name(obj, None)} is bound to its owner."
            if no_drop and not system_move:
                current_holder = holder_of(obj.location)
                # The moving item itself may only return to its holder's hands
                # (no direct "put"); nested ones only need to keep that holder.
                allowed = destination if obj is item else new_holder
                if current_holder is not None and allowed is not current_holder:
                    return f"{_name(obj, None)} cannot leave its holder."
        except TransferPolicyError:
            logger.log_err(f"Transfer policy blocked malformed item #{obj.id}.")
            return "That item needs staff repair."
    return None


_OPERATION_MESSAGES = {
    "drop": "You cannot drop {name}.",
    "give": "You cannot give {name} away.",
    "put": "You cannot put {name} into anything.",
    "junk": "You cannot junk {name}.",
    "sell": "That item cannot be traded.",
}


def transfer_denial(
    item: Any, actor: Any | None, operation: str, destination: Any = None
) -> str | None:
    """Return one player-safe denial for a command-level transfer, or None.

    ``no_drop`` forbids every voluntary release; ``account_bound`` forbids all
    of them except ``put`` inside the owner's own carried tree. The possession
    rule is then checked against ``destination`` when one is supplied.
    """
    if operation not in OPERATIONS:
        raise TransferPolicyError(f"Unknown transfer operation {operation!r}.")
    try:
        no_drop, account_bound = transfer_flags(item)
    except TransferPolicyError:
        return "That item needs staff repair."
    template = _OPERATION_MESSAGES.get(operation)
    if template and (no_drop or (account_bound and operation != "put")):
        return template.format(name=_name(item, actor))
    if destination is None:
        return None
    denial = move_denial(item, destination)
    if denial and actor is not None:
        if account_bound:
            whom = "someone else" if operation == "get" else "you"
            return f"{_name(item, actor)} is bound to {whom}."
        if template:
            return template.format(name=_name(item, actor))
    return denial


def retain_bound_items(owner: Any) -> tuple[Any, ...]:
    """COMBAT-05 death hook: keep bound items with their dying owner.

    Nested bound items are lifted into the owner's direct inventory so their
    public containers can still enter the corpse. Returns every direct item the
    corpse transfer must skip. Anomalous bound items (on NPCs or bound to
    someone else) are also held back and logged for staff rather than exposed.
    """
    retained = []
    for obj in _subtree(owner)[1:]:
        try:
            if not transfer_flags(obj)[1]:
                continue
            owner_id = effective_owner_id(obj)
        except TransferPolicyError:
            owner_id = None
        if owner_id != owner.id:
            logger.log_err(
                f"Death retained anomalous bound item #{obj.id} on #{owner.id}."
            )
        if obj.location is not owner and not obj.move_to(
            owner,
            quiet=True,
            move_type="bound_retention",
            transfer_bypass="death retention",
            encumbrance_bypass="death bound retention",
        ):
            raise TransferPolicyError(f"Could not retain bound item #{obj.id}.")
        bind_to_holder(obj)
        retained.append(obj)
    return tuple(retained)


def validate_flag_change(item: Any, name: str, value: bool) -> None:
    """Builder guard for live items: no NPC bound items and no silent rebinding."""
    if name not in (NO_DROP_ATTRIBUTE, ACCOUNT_BOUND_ATTRIBUTE):
        raise ValueError("unknown transfer flag.")
    if not isinstance(value, bool):
        raise ValueError("must be on or off.")
    if name != ACCOUNT_BOUND_ATTRIBUTE:
        return
    if value and str(item.attributes.get("type") or "") == "money":
        raise ValueError("money piles cannot be account-bound.")
    holder = holder_of(item.location)
    if value and holder is not None and not is_pc(holder):
        raise ValueError("NPCs cannot carry account-bound items.")
    try:
        bound = bound_owner_id(item) is not None
    except TransferPolicyError:
        bound = True
    if bound and not value:
        raise ValueError("this item is bound; use itempolicy/unbind first.")


def record_audit(
    item: Any,
    actor: Any,
    operation: str,
    before: Any,
    after: Any,
    reason: str,
) -> None:
    """Keep a bounded primitive audit trail on the item and in the server log."""
    entry = {
        "actor": getattr(actor, "id", None),
        "operation": operation,
        "before": before,
        "after": after,
        "reason": reason[:MAX_REASON_LENGTH],
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    history = list(item.attributes.get(AUDIT_ATTRIBUTE) or [])[-MAX_AUDIT_ENTRIES + 1 :]
    item.attributes.add(AUDIT_ATTRIBUTE, [*history, entry])
    logger.log_info(
        f"Item policy {operation}: item #{item.id} by #{entry['actor']} "
        f"{before!r} -> {after!r} ({entry['reason']})"
    )


def _require_reason(reason: Any) -> str:
    """Every staff repair must carry a bounded, non-empty explanation."""
    if not isinstance(reason, str) or not reason.strip():
        raise TransferPolicyError("A staff repair needs a reason.")
    return reason.strip()[:MAX_REASON_LENGTH]


def staff_move(item: Any, destination: Any, actor: Any, reason: str) -> None:
    """Relocate an item past transfer and capacity policy with an audit entry."""
    reason = _require_reason(reason)
    before = getattr(item.location, "id", None)
    if not item.move_to(
        destination,
        quiet=True,
        move_type="staff_repair",
        transfer_bypass=f"staff repair by #{actor.id}: {reason}",
        encumbrance_bypass=f"staff item repair: {reason}",
    ):
        raise TransferPolicyError("That item could not be moved there.")
    record_audit(item, actor, "move", before, destination.id, reason)


def staff_unbind(item: Any, actor: Any, reason: str) -> None:
    """Clear a binding record so the next PC grant binds the item afresh."""
    reason = _require_reason(reason)
    before = item.attributes.get(BINDING_ATTRIBUTE)
    if before is None:
        raise TransferPolicyError("That item is not bound.")
    item.attributes.remove(BINDING_ATTRIBUTE)
    record_audit(item, actor, "unbind", dict(before), None, reason)

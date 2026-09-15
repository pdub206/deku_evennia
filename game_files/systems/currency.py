"""Canonical ITEM-02 wallet and physical-money services."""

from __future__ import annotations

from ast import literal_eval
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import transaction
from evennia import create_object

CURRENCY_ATTRIBUTE = "currency"
LEDGER_ATTRIBUTE = "currency_ledger"
DEFAULT_MAX_BALANCE = 2_000_000_000
DEFAULT_LEDGER_LIMIT = 100


class CurrencyError(ValueError):
    """Raised when wallet data or a requested mutation is invalid."""


@dataclass(frozen=True)
class CurrencyResult:
    """Immutable result of one wallet operation."""

    success: bool
    amount: int
    before: int
    after: int
    transaction_id: str
    outcome: str
    repeated: bool = False


def balance(owner: Any) -> int:
    """Return a validated wallet balance."""
    value = owner.attributes.get(CURRENCY_ATTRIBUTE, default=0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CurrencyError("Currency must be a non-negative whole number.")
    if value > maximum_balance():
        raise CurrencyError("Currency exceeds the configured maximum balance.")
    return value


def maximum_balance() -> int:
    """Return validated server policy for wallet balances."""
    value = getattr(settings, "GAME_MAX_CURRENCY", DEFAULT_MAX_BALANCE)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CurrencyError("GAME_MAX_CURRENCY must be a positive integer.")
    return value


def format_coins(amount: int) -> str:
    """Format a validated amount with its singular or plural unit."""
    _validate_amount(amount, allow_zero=True)
    return f"{amount} coin" if amount == 1 else f"{amount} coins"


def credit(
    owner: Any,
    amount: int,
    transaction_id: str,
    *,
    actor: Any = None,
    source: str,
    reason: str = "",
) -> CurrencyResult:
    """Credit one wallet once without accepting a signed delta."""
    return _mutate(owner, amount, transaction_id, "credit", actor, source, reason)


def debit(
    owner: Any,
    amount: int,
    transaction_id: str,
    *,
    actor: Any = None,
    source: str,
    reason: str = "",
) -> CurrencyResult:
    """Debit one wallet once without accepting a signed delta."""
    return _mutate(owner, amount, transaction_id, "debit", actor, source, reason)


def transfer(
    sender: Any,
    recipient: Any,
    amount: int,
    transaction_id: str,
    *,
    actor: Any = None,
    source: str,
    reason: str = "",
) -> CurrencyResult:
    """Atomically transfer funds between distinct wallets exactly once."""
    _validate_request(amount, transaction_id, source, reason)
    if sender is recipient or sender.id == recipient.id:
        raise CurrencyError("You cannot give coins to yourself.")
    with transaction.atomic():
        _lock_owners(sender, recipient)
        prior = _find_entry(sender, transaction_id)
        if prior:
            return _result_from_entry(prior, repeated=True)
        sender_before, recipient_before = balance(sender), balance(recipient)
        if sender_before < amount:
            return _record_failure(
                sender,
                amount,
                transaction_id,
                "transfer",
                actor,
                source,
                reason,
                sender_before,
                "insufficient funds",
            )
        if recipient_before + amount > maximum_balance():
            return _record_failure(
                sender,
                amount,
                transaction_id,
                "transfer",
                actor,
                source,
                reason,
                sender_before,
                "recipient wallet is full",
            )
        sender.attributes.add(CURRENCY_ATTRIBUTE, sender_before - amount)
        recipient.attributes.add(CURRENCY_ATTRIBUTE, recipient_before + amount)
        result = CurrencyResult(
            True,
            amount,
            sender_before,
            sender_before - amount,
            transaction_id,
            "success",
        )
        _append_entry(sender, result, "transfer", actor, recipient, source, reason)
        _append_entry(
            recipient,
            CurrencyResult(
                True,
                amount,
                recipient_before,
                recipient_before + amount,
                transaction_id,
                "success",
            ),
            "receive",
            actor,
            sender,
            source,
            reason,
        )
        return result


def create_pile(
    location: Any, amount: int, transaction_id: str, *, weight: float = 0.0
) -> Any:
    """Create one validated physical money pile at a location."""
    _validate_request(amount, transaction_id, "money-pile", "")
    pile = create_object(
        "typeclasses.objects.Item", key=format_coins(amount), location=location
    )
    pile.db.type = "money"
    pile.db.amount = amount
    pile.db.money_transaction_id = transaction_id
    pile.db.weight = weight
    return pile


def pile_amount(pile: Any) -> int:
    """Return a validated positive amount from a physical money pile."""
    if str(pile.attributes.get("type") or "").casefold() != "money":
        raise CurrencyError("That is not a money pile.")
    amount = pile.attributes.get("amount")
    _validate_amount(amount)
    return amount


def pickup_pile(pile: Any, owner: Any, transaction_id: str) -> CurrencyResult:
    """Credit and consume one exact pile atomically."""
    amount = pile_amount(pile)
    with transaction.atomic():
        result = credit(
            owner,
            amount,
            transaction_id,
            actor=owner,
            source=f"pile:{pile.id}",
            reason="pickup",
        )
        if result.success and not result.repeated:
            pile.delete()
        return result


def audit_entries(owner: Any) -> tuple[dict[str, Any], ...]:
    """Return a read-only view of the bounded staff audit ledger."""
    return tuple(_ledger(owner))


def repair(
    owner: Any,
    amount: int,
    transaction_id: str,
    *,
    actor: Any,
    source: str,
    reason: str,
) -> CurrencyResult:
    """Replace malformed wallet data through an explicit audited staff seam."""
    _validate_amount(amount, allow_zero=True)
    _validate_request(max(amount, 1), transaction_id, source, reason)
    with transaction.atomic():
        _lock_owners(owner)
        raw = owner.attributes.get(CURRENCY_ATTRIBUTE, default=0)
        before = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
        owner.attributes.add(CURRENCY_ATTRIBUTE, amount)
        result = CurrencyResult(
            True, amount, before, amount, transaction_id, "repaired"
        )
        _append_entry(owner, result, "repair", actor, owner, source, reason)
        return result


def _mutate(
    owner: Any,
    amount: int,
    transaction_id: str,
    operation: str,
    actor: Any,
    source: str,
    reason: str,
) -> CurrencyResult:
    _validate_request(amount, transaction_id, source, reason)
    with transaction.atomic():
        _lock_owners(owner)
        prior = _find_entry(owner, transaction_id)
        if prior:
            return _result_from_entry(prior, repeated=True)
        before = balance(owner)
        if operation == "debit" and before < amount:
            return _record_failure(
                owner,
                amount,
                transaction_id,
                operation,
                actor,
                source,
                reason,
                before,
                "insufficient funds",
            )
        after = before + amount if operation == "credit" else before - amount
        if after > maximum_balance():
            return _record_failure(
                owner,
                amount,
                transaction_id,
                operation,
                actor,
                source,
                reason,
                before,
                "maximum balance exceeded",
            )
        owner.attributes.add(CURRENCY_ATTRIBUTE, after)
        result = CurrencyResult(True, amount, before, after, transaction_id, "success")
        _append_entry(owner, result, operation, actor, owner, source, reason)
        return result


def _validate_amount(amount: int, *, allow_zero: bool = False) -> None:
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount < (0 if allow_zero else 1)
    ):
        raise CurrencyError("Amount must be a positive whole number.")
    if amount > maximum_balance():
        raise CurrencyError("Amount exceeds the configured maximum balance.")


def _validate_request(
    amount: int, transaction_id: str, source: str, reason: str
) -> None:
    _validate_amount(amount)
    if not all(
        isinstance(value, str) and value.strip() for value in (transaction_id, source)
    ):
        raise CurrencyError("A stable transaction identity and source are required.")
    if not isinstance(reason, str):
        raise CurrencyError("Transaction reason must be text.")


def _lock_owners(*owners: Any) -> None:
    from evennia.objects.models import ObjectDB

    list(
        ObjectDB.objects.select_for_update().filter(
            id__in=sorted(owner.id for owner in owners)
        )
    )


def _ledger(owner: Any) -> list[dict[str, Any]]:
    # Read through a fresh ORM instance so a long-lived typeclass's negative
    # Attribute cache cannot hide a ledger created earlier in this process.
    from evennia.objects.models import ObjectDB

    stored_owner = ObjectDB.objects.get(id=owner.id)
    if not stored_owner.attributes.has(LEDGER_ATTRIBUTE):
        return []
    value = stored_owner.attributes.get(LEDGER_ATTRIBUTE)
    return list(value) if isinstance(value, list) else []


def _find_entry(owner: Any, transaction_id: str) -> dict[str, Any] | None:
    from evennia.objects.models import ObjectDB

    stored_owner = ObjectDB.objects.get(id=owner.id)
    tag = stored_owner.tags.get(
        _transaction_attribute(transaction_id),
        category="currency_transaction",
        return_tagobj=True,
    )
    value = getattr(tag, "db_data", None)
    if isinstance(value, str):
        try:
            value = literal_eval(value)
        except (SyntaxError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def _append_entry(
    owner: Any,
    result: CurrencyResult,
    operation: str,
    actor: Any,
    target: Any,
    source: str,
    reason: str,
) -> None:
    entries = _ledger(owner)
    entry = {
        "transaction_id": result.transaction_id,
        "operation": operation,
        "amount": result.amount,
        "before": result.before,
        "after": result.after,
        "actor_id": getattr(actor, "id", None),
        "target_id": getattr(target, "id", None),
        "source": source,
        "reason": reason,
        "time": datetime.now(timezone.utc).isoformat(),
        "outcome": result.outcome,
        "success": result.success,
    }
    entries.append(entry)
    limit = getattr(settings, "GAME_CURRENCY_LEDGER_LIMIT", DEFAULT_LEDGER_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise CurrencyError("GAME_CURRENCY_LEDGER_LIMIT must be a positive integer.")
    discarded = entries[:-limit]
    owner.attributes.add(LEDGER_ATTRIBUTE, entries[-limit:])
    owner.tags.add(
        _transaction_attribute(result.transaction_id),
        category="currency_transaction",
        data=entry,
    )
    for old_entry in discarded:
        old_id = old_entry.get("transaction_id")
        if isinstance(old_id, str):
            owner.tags.remove(
                _transaction_attribute(old_id), category="currency_transaction"
            )


def _transaction_attribute(transaction_id: str) -> str:
    """Map an arbitrary stable identity to a bounded Attribute key."""
    digest = sha256(transaction_id.encode("utf-8")).hexdigest()
    return f"currency_transaction_{digest}"


def _record_failure(
    owner: Any,
    amount: int,
    transaction_id: str,
    operation: str,
    actor: Any,
    source: str,
    reason: str,
    before: int,
    outcome: str,
) -> CurrencyResult:
    result = CurrencyResult(False, amount, before, before, transaction_id, outcome)
    _append_entry(owner, result, operation, actor, owner, source, reason)
    return result


def _result_from_entry(entry: dict[str, Any], *, repeated: bool) -> CurrencyResult:
    return CurrencyResult(
        bool(entry["success"]),
        int(entry["amount"]),
        int(entry["before"]),
        int(entry["after"]),
        str(entry["transaction_id"]),
        str(entry["outcome"]),
        repeated,
    )

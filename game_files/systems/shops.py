"""Validated shop definitions, stock scheduling, and atomic live trades."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from evennia.prototypes.prototypes import PROTOTYPE_TAG_CATEGORY
from systems.combat import is_fighting
from systems.encumbrance import spawn_with_capacity
from systems.item_transfer import transfer_denial
from world.build_schema import ITEM_TYPES

SHOP_STOCK_PROVENANCE_ATTRIBUTE = "shop_stock_provenance"
SHOP_RESTOCK_STATE_ATTRIBUTE = "shop_restock_state"
SHOP_PROFILE_VERSION = 1
MIN_PRICE_PERCENT = 0
MAX_PRICE_PERCENT = 1000
MAX_STOCK_QUANTITY = 1000


class ShopError(ValueError):
    """Raised when authored or persisted shop data is unsafe or invalid."""


@dataclass(frozen=True)
class ShopAvailability:
    """A fail-closed result shared by requests and later transaction commits."""

    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ShopRestockResult:
    """The deterministic result of one schedule/reset adapter invocation."""

    status: str
    created: int = 0
    reason: str = ""


def validate_shop_profile(raw: Any) -> dict[str, Any]:
    """Validate and detach one primitive-only shopkeeper service profile."""
    expected = {
        "version",
        "profile_key",
        "access_lock",
        "accepted_kinds",
        "buy_markup",
        "sell_markdown",
        "open_hour",
        "close_hour",
        "wallet_opening_balance",
        "stock",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ShopError("Shop profile has an invalid shape.")
    if raw["version"] != SHOP_PROFILE_VERSION:
        raise ShopError("Shop profile has an unsupported version.")
    profile_key = _stable_key(raw["profile_key"], "profile key")
    access_lock = raw["access_lock"]
    if (
        not isinstance(access_lock, str)
        or not access_lock.startswith("shop:")
        or "\n" in access_lock
        or not access_lock.removeprefix("shop:").strip()
    ):
        raise ShopError("Shop access lock must define the 'shop' access type.")
    kinds = raw["accepted_kinds"]
    if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence):
        raise ShopError("Accepted item kinds must be a list.")
    known_kinds = {"item", *ITEM_TYPES}
    if (
        not kinds
        or len(set(kinds)) != len(kinds)
        or any(k not in known_kinds for k in kinds)
    ):
        raise ShopError("Accepted item kinds must be unique known kinds.")
    buy_markup = _bounded_int(
        raw["buy_markup"], "buy markup", MIN_PRICE_PERCENT, MAX_PRICE_PERCENT
    )
    sell_markdown = _bounded_int(
        raw["sell_markdown"], "sell markdown", MIN_PRICE_PERCENT, MAX_PRICE_PERCENT
    )
    if sell_markdown > buy_markup:
        raise ShopError("A shop's sell price cannot exceed its buy price.")
    open_hour = _bounded_int(raw["open_hour"], "open hour", 0, 23)
    close_hour = _bounded_int(raw["close_hour"], "close hour", 0, 23)
    if open_hour == close_hour:
        raise ShopError("Shop open and close hours must differ.")
    opening_balance = _bounded_int(
        raw["wallet_opening_balance"], "wallet opening balance", 0, 2_000_000_000
    )
    stock = raw["stock"]
    if isinstance(stock, (str, bytes)) or not isinstance(stock, Sequence):
        raise ShopError("Shop stock must be a list.")
    normalized_stock = []
    seen = set()
    for entry in stock:
        if not isinstance(entry, Mapping) or set(entry) != {
            "prototype_key",
            "target_quantity",
        }:
            raise ShopError("A shop stock entry has an invalid shape.")
        prototype_key = _stable_key(entry["prototype_key"], "stock prototype key")
        if prototype_key in seen:
            raise ShopError("Shop stock prototypes must be unique.")
        prototype = _item_prototype(prototype_key)
        kind = prototype.get("type") or "item"
        if kind not in kinds:
            raise ShopError("Shop stock contains an unaccepted item kind.")
        quantity = _bounded_int(
            entry["target_quantity"], "stock target quantity", 0, MAX_STOCK_QUANTITY
        )
        seen.add(prototype_key)
        normalized_stock.append(
            {"prototype_key": prototype_key, "target_quantity": quantity}
        )
    return {
        "version": SHOP_PROFILE_VERSION,
        "profile_key": profile_key,
        "access_lock": access_lock,
        "accepted_kinds": list(kinds),
        "buy_markup": buy_markup,
        "sell_markdown": sell_markdown,
        "open_hour": open_hour,
        "close_hour": close_hour,
        "wallet_opening_balance": opening_balance,
        "stock": normalized_stock,
    }


def initialize_shopkeeper(npc: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Install one detached live profile and initialize its finite wallet once."""
    if getattr(getattr(npc, "db", None), "is_player_character", None) is not False:
        raise ShopError("Shop profiles may be installed only on NPCs.")
    normalized = validate_shop_profile(profile)
    npc.locks.add(normalized["access_lock"])
    if npc.attributes.get("currency") is None:
        npc.attributes.add("currency", normalized["wallet_opening_balance"])
    return deepcopy(normalized)


def shop_is_open(profile: Mapping[str, Any], hour: int) -> bool:
    """Return whether a validated daytime or overnight range includes ``hour``."""
    normalized = validate_shop_profile(profile)
    hour = _bounded_int(hour, "world hour", 0, 23)
    opening, closing = normalized["open_hour"], normalized["close_hour"]
    return (
        opening <= hour < closing
        if opening < closing
        else hour >= opening or hour < closing
    )


def shop_availability(
    npc: Any, actor: Any, profile: Mapping[str, Any], hour: int
) -> ShopAvailability:
    """Revalidate every mutable shopkeeper condition at an operation boundary."""
    try:
        normalized = validate_shop_profile(profile)
        if getattr(npc.db, "is_player_character", None) is not False:
            return ShopAvailability(False, "not_npc")
        if npc.location is None or actor is None or actor.location is not npc.location:
            return ShopAvailability(False, "absent")
        if not shop_is_open(normalized, hour):
            return ShopAvailability(False, "closed")
        if npc.attributes.get("hp_current", default=1) <= 0:
            return ShopAvailability(False, "dead")
        if is_fighting(npc):
            return ShopAvailability(False, "fighting")
        if not npc.access(actor, "shop"):
            return ShopAvailability(False, "access_denied")
    except Exception:
        return ShopAvailability(False, "malformed")
    return ShopAvailability(True)


def process_shop_schedule(
    npc: Any, profile: Mapping[str, Any], *, day: int, hour: int
) -> ShopRestockResult:
    """Top up authored stock once on the first eligible event of one world day.

    This is ENV-01/AREA-03's idempotent adapter. A missed day is never replayed:
    the state remembers only the latest consumed day for each profile entry.
    """
    try:
        normalized = validate_shop_profile(profile)
        if getattr(npc.db, "is_player_character", None) is not False:
            return ShopRestockResult("blocked", reason="not_npc")
        day = _bounded_int(day, "world day", 0, 2_000_000_000)
        hour = _bounded_int(hour, "world hour", 0, 23)
        if not shop_is_open(normalized, hour) or npc.location is None:
            return ShopRestockResult("declined", reason="not_eligible")
        if npc.attributes.get("hp_current", default=1) <= 0 or is_fighting(npc):
            return ShopRestockResult("declined", reason="unavailable")
        state = _restock_state(npc)
        created = 0
        profile_key = normalized["profile_key"]
        for index, entry in enumerate(normalized["stock"]):
            entry_key = f"{profile_key}:{index}"
            prior_day = state["entries"].get(entry_key)
            if prior_day is not None and day <= prior_day:
                continue
            # Consume before spawning: failure is durable and cannot duplicate on reload.
            state["entries"][entry_key] = day
            npc.attributes.add(SHOP_RESTOCK_STATE_ATTRIBUTE, deepcopy(state))
            current = len(
                _authored_copies(npc, profile_key, index, entry["prototype_key"])
            )
            for _ in range(max(0, entry["target_quantity"] - current)):
                spawned = spawn_with_capacity(
                    _item_prototype(entry["prototype_key"]), npc
                )
                if not spawned:
                    break
                for item in spawned:
                    item.attributes.add(
                        SHOP_STOCK_PROVENANCE_ATTRIBUTE,
                        {
                            "version": 1,
                            "profile_key": profile_key,
                            "stock_index": index,
                            "prototype_key": entry["prototype_key"],
                        },
                    )
                created += len(spawned)
        return ShopRestockResult("restocked", created=created)
    except Exception:
        return ShopRestockResult("blocked", reason="malformed")


def shop_snapshot(npc: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return Builder-safe definition and live stock views as separate data."""
    normalized = validate_shop_profile(profile)
    definition = {
        key: deepcopy(value)
        for key, value in normalized.items()
        if key != "access_lock"
    }
    definition["access"] = "configured"
    live = []
    for index, entry in enumerate(normalized["stock"]):
        authored = _authored_copies(
            npc, normalized["profile_key"], index, entry["prototype_key"]
        )
        actual = [
            item
            for item in npc.contents
            if entry["prototype_key"]
            in item.tags.get(category=PROTOTYPE_TAG_CATEGORY, return_list=True)
        ]
        live.append(
            {
                **entry,
                "actual_quantity": len(actual),
                "authored_quantity": len(authored),
            }
        )
    return {"definition": definition, "live_stock": live}


def _authored_copies(
    npc: Any, profile_key: str, index: int, prototype_key: str
) -> list[Any]:
    return [
        item
        for item in npc.contents
        if item.attributes.get(SHOP_STOCK_PROVENANCE_ATTRIBUTE)
        == {
            "version": 1,
            "profile_key": profile_key,
            "stock_index": index,
            "prototype_key": prototype_key,
        }
    ]


def _restock_state(npc: Any) -> dict[str, Any]:
    raw = npc.attributes.get(SHOP_RESTOCK_STATE_ATTRIBUTE)
    if raw is None:
        return {"version": 1, "entries": {}}
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "entries"}
        or raw["version"] != 1
        or not isinstance(raw["entries"], Mapping)
    ):
        raise ShopError("Shop restock state is malformed.")
    entries = {}
    for key, value in raw["entries"].items():
        _stable_key(key, "restock entry")
        entries[key] = _bounded_int(value, "restock day", 0, 2_000_000_000)
    return {"version": 1, "entries": entries}


def _item_prototype(key: str) -> dict[str, Any]:
    from systems.prototype_catalogs import (
        PrototypeCatalogError,
        resolve_runtime_prototype,
    )

    try:
        return resolve_runtime_prototype(key, kind="item")
    except PrototypeCatalogError:
        raise ShopError("Shop stock references an unknown or ambiguous item prototype.")


def _stable_key(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 80
        or not all(ch.isalnum() or ch in "._:-" for ch in value)
    ):
        raise ShopError(f"Shop {label} is invalid.")
    return value


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ShopError(f"Shop {label} must be between {minimum} and {maximum}.")
    return value


def current_shop_hour() -> int:
    """Read shop hours from the canonical persisted world calendar."""
    from systems.world_clock import clock_state

    return clock_state()["minute"] // 60 % 24


def shop_profile(npc: Any) -> dict[str, Any]:
    """Read exactly one validated shopkeeper assignment from a live NPC."""
    from systems.mobile_specials import mobile_specials

    entries = [
        e["config"]
        for e in mobile_specials(npc)["behaviors"]
        if e["key"] == "shopkeeper"
    ]
    if len(entries) != 1:
        raise ShopError("That NPC does not have one valid shop.")
    return validate_shop_profile(entries[0])


def set_shop_profile(npc: Any, profile: Mapping[str, Any]) -> None:
    """Attach a shop to this exact NPC while preserving its other specials."""
    from systems.mobile_specials import mobile_specials, set_mobile_specials

    assignment = mobile_specials(npc)
    assignment["behaviors"] = [
        e for e in assignment["behaviors"] if e["key"] != "shopkeeper"
    ]
    assignment["behaviors"].append(
        {"key": "shopkeeper", "config": validate_shop_profile(profile)}
    )
    set_mobile_specials(npc, assignment)


def shop_price(item: Any, profile: Mapping[str, Any], *, buying: bool) -> int:
    """Buy rounds up and sell rounds down, with a one-coin nonzero minimum."""
    normalized = validate_shop_profile(profile)
    value = item.attributes.get("value", default=0)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ShopError("Shops only trade items with a positive whole-coin value.")
    percent = normalized["buy_markup" if buying else "sell_markdown"]
    return max(1, (value * percent + (99 if buying else 0)) // 100)


def trade_eligible(
    item: Any,
    actor: Any,
    profile: Mapping[str, Any],
    *,
    buying: bool,
    check_access: bool = True,
) -> None:
    """ITEM-05 seam plus the shop-specific exclusions, shared by quotes/trades."""
    if not item.is_typeclass("typeclasses.objects.Item", exact=False):
        raise ShopError("That item cannot be traded.")
    kind = item.attributes.get("type") or "item"
    if (
        kind in {"money", "corpse"}
        or item.is_typeclass("typeclasses.objects.Corpse", exact=False)
        or kind not in profile["accepted_kinds"]
        or item.contents
        or item.attributes.get("worn_location")
        or transfer_denial(item, actor, "sell")
    ):
        raise ShopError("That item cannot be traded.")
    if check_access and not item.access(
        actor, "get" if buying else "drop", default=True
    ):
        raise ShopError("That item cannot be traded.")
    shop_price(item, profile, buying=buying)


def visible_stock(npc: Any, actor: Any) -> list[Any]:
    """Expose eligible actual inventory, including previously sold items."""
    from systems.visibility import room_visibility, target_visibility

    if not room_visibility(actor, actor.location).visible:
        return []
    profile = shop_profile(npc)
    items = []
    for item in sorted(npc.contents, key=lambda obj: (obj.key.casefold(), obj.id)):
        if not target_visibility(actor, item, source=npc).visible:
            continue
        try:
            trade_eligible(item, actor, profile, buying=True)
        except ShopError:
            continue
        items.append(item)
    return items


@dataclass(frozen=True)
class ShopTradeResult:
    """Durable outcome; repeated requests never move funds or emit messages."""

    success: bool
    transaction_id: str
    price: int = 0
    reason: str = ""
    repeated: bool = False


_ACTIVE_SHOP_TRADES: set[int] = set()


def _refresh_trade_state(objects: Sequence[tuple[Any, int]]) -> None:
    """Reload ORM fields and Attribute values without reusing stale identity-map data."""
    from evennia.objects.models import ObjectDB

    for obj, object_id in objects:
        obj.__dict__.update(ObjectDB.objects.filter(id=object_id).values().get())
        obj._is_deleted = False
        type(obj).cache_instance(obj)
        obj._state.fields_cache.clear()
        obj.attributes.reset_cache()
        rows = {row["id"]: row for row in obj.db_attributes.values()}
        for attribute in obj.db_attributes.all():
            attribute.__dict__.update(rows[attribute.id])
        obj.locks.reset()
    for obj, _ in objects:
        obj.contents_cache.init()


def trade_shop(
    actor: Any, npc: Any, item: Any, *, buying: bool, transaction_id: str
) -> ShopTradeResult:
    """Commit the exact item and conserved wallets in one database transaction.

    Permanent per-actor receipts survive currency-ledger pruning and reloads.
    Hooks run inside the rollback boundary; messages are attempted only after
    commit. Delivery is at most once (a disconnected client cannot be promised
    receipt), and a failed message never retries a committed financial action.
    """
    import json
    from hashlib import sha256

    from django.db import transaction
    from evennia.objects.models import ObjectDB
    from evennia.utils import logger
    from systems.currency import CurrencyError, balance, transfer
    from systems.encumbrance import can_receive
    from systems.visibility import target_visibility

    if (
        not isinstance(transaction_id, str)
        or not transaction_id.strip()
        or len(transaction_id) > 200
    ):
        raise ShopError("A bounded stable transaction identity is required.")
    receipt_key = "shop_trade_" + sha256(transaction_id.encode()).hexdigest()
    request = [npc.id, item.id, buying]
    objects = (actor, npc, item)
    if any(obj.id in _ACTIVE_SHOP_TRADES for obj in objects):
        return ShopTradeResult(
            False, transaction_id, reason="That trade is already in progress."
        )
    object_ids = tuple(obj.id for obj in objects)
    _ACTIVE_SHOP_TRADES.update(object_ids)
    source, destination = (npc, actor) if buying else (actor, npc)
    original_room = actor.location
    try:
        with transaction.atomic():
            list(
                ObjectDB.objects.select_for_update()
                .filter(id__in=sorted(obj.id for obj in objects))
                .order_by("id")
            )
            _refresh_trade_state(tuple(zip(objects, object_ids)))
            receipt = actor.attributes.get(receipt_key)
            if receipt is not None:
                receipt = json.loads(receipt)
                if receipt["request"] != request:
                    return ShopTradeResult(
                        False,
                        transaction_id,
                        reason="Transaction identity was already used.",
                        repeated=True,
                    )
                return ShopTradeResult(
                    receipt["success"],
                    transaction_id,
                    receipt["price"],
                    receipt["reason"],
                    True,
                )
            try:
                # A savepoint also rolls back mutations made by user-defined hooks.
                with transaction.atomic():
                    profile = shop_profile(npc)
                    price = shop_price(item, profile, buying=buying)

                    def revalidate(*, moved: bool = False) -> None:
                        """Reject changed prices, access, ownership, or shop definition."""
                        live = shop_profile(npc)
                        if (
                            live != profile
                            or shop_price(item, live, buying=buying) != price
                        ):
                            raise ShopError(
                                "The shop price or definition changed. Try again."
                            )
                        if not shop_availability(
                            npc, actor, live, current_shop_hour()
                        ).available:
                            raise ShopError("That shop is unavailable.")
                        if (
                            actor.location is not original_room
                            or not target_visibility(actor, npc).visible
                        ):
                            raise ShopError("That shop is unavailable.")
                        if item.location is not (destination if moved else source):
                            raise ShopError("That item is no longer available.")
                        if not target_visibility(
                            actor, item, source=item.location
                        ).visible:
                            raise ShopError("That item is unavailable.")
                        trade_eligible(
                            item, actor, live, buying=buying, check_access=not moved
                        )

                    revalidate()
                    initial_wallets = (balance(destination), balance(source))
                    if not item.at_pre_give(source, destination):
                        raise ShopError("That item cannot be transferred.")
                    if buying:
                        if not item.at_pre_get(actor):
                            raise ShopError("That item cannot be transferred.")
                    elif not item.at_pre_drop(actor):
                        raise ShopError("That item cannot be transferred.")
                    revalidate()
                    if (balance(destination), balance(source)) != initial_wallets:
                        raise ShopError("The shop wallets changed. Try again.")
                    admission = can_receive(destination, item)
                    if not admission.allowed:
                        raise ShopError(
                            admission.message or "There is no room for that item."
                        )
                    payment = transfer(
                        destination,
                        source,
                        price,
                        f"shop:{actor.id}:" + transaction_id,
                        actor=actor,
                        source=f"shop:{npc.id}",
                        reason="buy" if buying else "sell",
                    )
                    if not payment.success or payment.repeated:
                        raise ShopError(payment.outcome)
                    if not item.move_to(
                        destination, quiet=True, move_type="shop", capacity_actor=actor
                    ):
                        raise ShopError("That item could not be transferred.")
                    item.at_give(source, destination)
                    revalidate(moved=True)
                    if (balance(destination), balance(source)) != (
                        initial_wallets[0] - price,
                        initial_wallets[1] + price,
                    ):
                        raise ShopError("The shop wallets changed. Try again.")
                    name = item.get_display_name(actor)
                    keeper = npc.get_display_name(actor)
                    room = original_room
                    public_name = item.get_display_name(room)
                    public_keeper = npc.get_display_name(room)
                    # Stock provenance belongs only to authored stock still owned by the shop.
                    if buying:
                        item.attributes.remove(SHOP_STOCK_PROVENANCE_ATTRIBUTE)
                    result = ShopTradeResult(True, transaction_id, price)
            except Exception as exc:
                moved_location = item.location
                _refresh_trade_state(tuple(zip(objects, object_ids)))
                for owner in (original_room, moved_location):
                    if owner is not None:
                        owner.contents_cache.init()
                reason = (
                    str(exc)
                    if isinstance(exc, (ShopError, CurrencyError))
                    else "That trade could not be completed."
                )
                result = ShopTradeResult(False, transaction_id, reason=reason)
            actor.attributes.add(
                receipt_key,
                json.dumps(
                    {
                        "request": request,
                        "success": result.success,
                        "price": result.price,
                        "reason": result.reason,
                    }
                ),
            )
            actor.attributes.add("shop_last_transaction", transaction_id)
            history = list(npc.attributes.get("shop_transaction_history", default=[]))
            history.append(
                {
                    "transaction_id": transaction_id,
                    "actor_id": actor.id,
                    "item_id": item.id,
                    "operation": "buy" if buying else "sell",
                    "success": result.success,
                    "price": result.price,
                    "reason": result.reason,
                }
            )
            npc.attributes.add("shop_transaction_history", history[-20:])
            if result.success:

                def announce() -> None:
                    """Attempt each committed message once, independently of delivery failures."""
                    verb = "buy" if buying else "sell"
                    prep = "from" if buying else "to"
                    try:
                        actor.msg(
                            f"You {verb} {name} {prep} {keeper} for {price} coins."
                        )
                    except Exception:
                        logger.log_err(
                            "Shop private message delivery failed after commit."
                        )
                    try:
                        room.msg_contents(
                            f"{actor.get_display_name(room)} {'buys' if buying else 'sells'} {public_name} {prep} {public_keeper}.",
                            exclude=actor,
                        )
                    except Exception:
                        logger.log_err(
                            "Shop public message delivery failed after commit."
                        )

                transaction.on_commit(announce)
            return result
    finally:
        _ACTIVE_SHOP_TRADES.difference_update(object_ids)

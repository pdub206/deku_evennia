"""ITEM-03A validated shop definitions, availability, and stock scheduling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from evennia.prototypes.prototypes import (PROTOTYPE_TAG_CATEGORY,
                                           search_prototype)
from systems.combat import is_fighting
from systems.encumbrance import spawn_with_capacity
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
    matches = [
        p
        for p in search_prototype(key)
        if p.get("prototype_key") == key
        and p.get("typeclass") == "typeclasses.objects.Item"
    ]
    if len(matches) != 1:
        raise ShopError("Shop stock references an unknown or ambiguous item prototype.")
    flat = {
        name: deepcopy(value) for name, value in matches[0].items() if name != "attrs"
    }
    for attr in matches[0].get("attrs", []):
        flat[attr[0]] = deepcopy(attr[1])
    return flat


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

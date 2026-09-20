"""ITEM-04A finite food and liquid consumption.

Food keeps its portions and receipts here.  Drink containers and fountains
reuse ITEM-05B's conserved liquid resource state; this module adds the closed,
code-owned liquid catalogue and the player-facing transaction rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from django.db import transaction
from systems.action_policy import ActionCategory
from systems.item_resources import (
    _identity,
    _refresh,
    _store,
    item_mutation,
    resource_profile,
    resource_state,
)

FOOD_ATTRIBUTE = "food_profile"
FOOD_STATE_ATTRIBUTE = "food_state"
LIQUID_ATTRIBUTE = "liquid_profile"
CONSUMABLE_VERSION = 1
MAX_PORTIONS = 10000


class ConsumableError(ValueError):
    """A safe denial or malformed consumable requiring Builder repair."""


@dataclass(frozen=True)
class LiquidDefinition:
    """Reviewed presentation and optional effect adapter for one liquid."""

    key: str
    display_name: str
    taste: str
    effect_key: str | None = None


LIQUIDS = MappingProxyType(
    {
        "water": LiquidDefinition("water", "water", "It tastes clean and plain."),
        "ale": LiquidDefinition("ale", "ale", "It tastes malty and bitter."),
    }
)
_EFFECTS: dict[str, Callable[[Any, Any], bool]] = {}


def register_consumable_effect(key: str, adapter: Callable[[Any, Any], bool]) -> None:
    """Register a reviewed code adapter; authored items may name only its key."""
    if not re.fullmatch(r"[a-z][a-z0-9_.]{1,63}", key) or not callable(adapter):
        raise ConsumableError("Invalid consumable effect adapter.")
    _EFFECTS[key] = adapter


def validate_food_profile(raw: Any) -> dict[str, Any]:
    """Validate primitive food portions and one optional registered effect key."""
    if not isinstance(raw, Mapping) or set(raw) != {"version", "portions", "effect"}:
        raise ConsumableError("Food profile has an invalid shape.")
    portions = raw["portions"]
    effect = raw["effect"]
    if (
        raw["version"] != CONSUMABLE_VERSION
        or isinstance(portions, bool)
        or not isinstance(portions, int)
        or not 1 <= portions <= MAX_PORTIONS
        or (
            effect is not None
            and (not isinstance(effect, str) or effect not in _EFFECTS)
        )
    ):
        raise ConsumableError("Food profile has invalid portions or effect.")
    return {"version": CONSUMABLE_VERSION, "portions": portions, "effect": effect}


def validate_liquid_profile(raw: Any, item_type: Any = None) -> dict[str, Any]:
    """Validate the liquid identity and whether a fountain is inexhaustible."""
    if not isinstance(raw, Mapping) or set(raw) != {
        "version",
        "liquid_key",
        "inexhaustible",
    }:
        raise ConsumableError("Liquid profile has an invalid shape.")
    key, inexhaustible = raw["liquid_key"], raw["inexhaustible"]
    if (
        raw["version"] != CONSUMABLE_VERSION
        or key not in LIQUIDS
        or not isinstance(inexhaustible, bool)
        or (item_type is not None and item_type not in {"drinkcon", "fountain"})
        or (item_type == "drinkcon" and inexhaustible)
    ):
        raise ConsumableError("Liquid profile has invalid data.")
    return {
        "version": CONSUMABLE_VERSION,
        "liquid_key": key,
        "inexhaustible": inexhaustible,
    }


def food_profile(item: Any) -> dict[str, Any]:
    """Read one strict food profile from a food item."""
    if item.db.type != "food":
        raise ConsumableError("That is not food.")
    return validate_food_profile(item.attributes.get(FOOD_ATTRIBUTE))


def food_state(item: Any) -> dict[str, Any]:
    """Return durable remaining portions and replay receipts."""
    profile = food_profile(item)
    raw = item.attributes.get(FOOD_STATE_ATTRIBUTE)
    if raw is None:
        return {
            "version": CONSUMABLE_VERSION,
            "remaining": profile["portions"],
            "receipts": {},
        }
    if not isinstance(raw, Mapping) or set(raw) != {"version", "remaining", "receipts"}:
        raise ConsumableError("Food state needs Builder repair.")
    remaining = raw["remaining"]
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, int)
        or not 0 <= remaining <= profile["portions"]
        or not isinstance(raw["receipts"], Mapping)
    ):
        raise ConsumableError("Food state needs Builder repair.")
    receipts = dict(raw["receipts"])
    if any(
        not isinstance(key, str) or value != "eat" for key, value in receipts.items()
    ):
        raise ConsumableError("Food state needs Builder repair.")
    return {"version": CONSUMABLE_VERSION, "remaining": remaining, "receipts": receipts}


def set_food_profile(item: Any, raw: Any) -> None:
    """Set validated authored food data without restoring already spent portions."""
    profile = validate_food_profile(raw)
    if item.db.type != "food":
        raise ConsumableError("Only food can have a food profile.")
    with item_mutation(item):
        state = food_state(item) if item.attributes.has(FOOD_ATTRIBUTE) else None
        item.attributes.add(FOOD_ATTRIBUTE, profile)
        if state is not None:
            state["remaining"] = min(state["remaining"], profile["portions"])
            item.attributes.add(FOOD_STATE_ATTRIBUTE, state)


def set_liquid_profile(item: Any, raw: Any) -> None:
    """Set reviewed liquid metadata after confirming it agrees with the resource."""
    profile = validate_liquid_profile(raw, item.db.type)
    resource = resource_profile(item)
    if (
        resource["kind"] != "liquid"
        or resource["resource_key"] != profile["liquid_key"]
    ):
        raise ConsumableError("Liquid and resource keys must match.")
    item.attributes.add(LIQUID_ATTRIBUTE, profile)


def _usable(actor: Any, item: Any, *, fountain: bool = False) -> None:
    decision = actor.actions.check(ActionCategory.MANIPULATE)
    if not decision.allowed:
        raise ConsumableError(decision.message)
    valid_location = item.location is actor or (
        fountain and item.location is actor.location
    )
    if (
        not valid_location
        or actor.location is None
        or not item.access(actor, "view", default=True)
        or not item.access(actor, "interact", default=True)
    ):
        raise ConsumableError("You cannot use that source.")
    if fountain != (item.db.type == "fountain"):
        raise ConsumableError("You must directly carry that item.")


def _liquid(item: Any) -> tuple[dict[str, Any], dict[str, Any], LiquidDefinition]:
    if item.db.type not in {"drinkcon", "fountain"}:
        raise ConsumableError("That is not a drink source.")
    profile, state = resource_profile(item), resource_state(item)
    liquid_profile = validate_liquid_profile(
        item.attributes.get(LIQUID_ATTRIBUTE), item.db.type
    )
    if (
        profile["kind"] != "liquid"
        or profile["resource_key"] != liquid_profile["liquid_key"]
    ):
        raise ConsumableError("That liquid needs Builder repair.")
    return profile, state, LIQUIDS[liquid_profile["liquid_key"]]


def taste(actor: Any, source: Any) -> str:
    """Describe a local liquid without changing its state."""
    with item_mutation(actor, source):
        _usable(actor, source, fountain=source.db.type == "fountain")
        _, state, liquid = _liquid(source)
        if not state["current"]:
            raise ConsumableError("It is empty.")
        return liquid.taste


def eat(actor: Any, item: Any, *, identity: str | None = None) -> bool:
    """Consume one food portion only after its adapter accepts the transaction."""
    identity = _identity(uuid4().hex if identity is None else identity)
    with item_mutation(actor, item):
        _usable(actor, item)
        profile, state = food_profile(item), food_state(item)
        if identity in state["receipts"]:
            return False
        if not state["remaining"]:
            raise ConsumableError("There is nothing left to eat.")
        effect = _EFFECTS.get(profile["effect"]) if profile["effect"] else None
        with transaction.atomic():
            if effect is not None and effect(actor, item) is not True:
                transaction.set_rollback(True)
            else:
                state["remaining"] -= 1
                state["receipts"][identity] = "eat"
                item.attributes.add(FOOD_STATE_ATTRIBUTE, state)
                if not state["remaining"]:
                    item.delete()
                return True
        _refresh(actor)
        raise ConsumableError("The food has no effect.")


def drink(actor: Any, source: Any, *, identity: str | None = None) -> bool:
    """Spend one liquid serving; fountains remain inexhaustible by definition."""
    identity = _identity(uuid4().hex if identity is None else identity)
    fountain = source.db.type == "fountain"
    with item_mutation(actor, source):
        _usable(actor, source, fountain=fountain)
        profile, state, liquid = _liquid(source)
        inexhaustible = validate_liquid_profile(
            source.attributes.get(LIQUID_ATTRIBUTE), source.db.type
        )["inexhaustible"]
        if identity in state["receipts"]:
            return False
        if not state["current"] and not inexhaustible:
            raise ConsumableError("It is empty.")
        effect = _EFFECTS.get(liquid.effect_key) if liquid.effect_key else None
        with transaction.atomic():
            if effect is not None and effect(actor, source) is not True:
                transaction.set_rollback(True)
            else:
                if not inexhaustible:
                    state["current"] -= 1
                state["receipts"][identity] = {
                    "operation": "spend",
                    "units": 1,
                    "peer": None,
                }
                _store(source, state)
                return True
        _refresh(actor)
        raise ConsumableError("The drink has no effect.")


def pour(
    actor: Any, source: Any, target: Any | None, *, identity: str | None = None
) -> int:
    """Move fitting whole liquid servings, or discard all contents when target is None."""
    identity = _identity(uuid4().hex if identity is None else identity)
    if source.db.type == "fountain":
        raise ConsumableError("A fountain cannot be poured from.")
    objects = (actor, source) if target is None else (actor, source, target)
    with item_mutation(*objects):
        _usable(actor, source)
        source_profile, source_state, _ = _liquid(source)
        if not source_state["current"]:
            raise ConsumableError("It is empty.")
        if target is None:
            units = source_state["current"]
            source_state["current"] = 0
            source_state["receipts"][identity] = {
                "operation": "spend",
                "units": units,
                "peer": None,
            }
            _store(source, source_state)
            return units
        _usable(actor, target)
        if target.db.type != "drinkcon":
            raise ConsumableError("Pour into a drink container or out.")
        target_profile, target_state, _ = _liquid(target)
        if (
            source_profile["resource_key"] != target_profile["resource_key"]
            and target_state["current"]
        ):
            raise ConsumableError("Those liquids cannot be mixed.")
        if source_profile["resource_key"] != target_profile["resource_key"]:
            # An empty container has no retained liquid. Keep its authored
            # capacity/recharge policy but atomically adopt the poured liquid.
            target_profile["resource_key"] = source_profile["resource_key"]
            target.attributes.add("item_resource", target_profile)
            target.attributes.add(
                LIQUID_ATTRIBUTE,
                {
                    "version": CONSUMABLE_VERSION,
                    "liquid_key": source_profile["resource_key"],
                    "inexhaustible": False,
                },
            )
        units = min(
            source_state["current"], target_profile["maximum"] - target_state["current"]
        )
        if not units:
            raise ConsumableError("The target is full.")
        source_state["current"] -= units
        target_state["current"] += units
        source_state["receipts"][identity] = {
            "operation": "source",
            "units": units,
            "peer": target.pk,
        }
        target_state["receipts"][identity] = {
            "operation": "refill",
            "units": units,
            "peer": source.pk,
        }
        _store(source, source_state)
        _store(target, target_state)
        return units

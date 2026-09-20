"""ITEM-05B finite resources, carried lights, and conserved replenishment.

Authored profiles contain only primitive data. Runtime receipts survive reload;
callbacks and player output occur only after a successful resource transaction.
Lights illuminate one room at Bright intensity, with no propagation or catchup.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

from django.db import transaction
from evennia.utils import logger
from systems.action_policy import ActionCategory

RESOURCE_ATTRIBUTE = "item_resource"
RESOURCE_STATE_ATTRIBUTE = "item_resource_state"
RESOURCE_VERSION = 1
MAX_RESOURCE_UNITS = 10000
LIGHT_RADIUS = "room"
_ACTIVE: set[int] = set()
_KIND_TYPES = {
    "fuel": {"light", "other"},
    "liquid": {"drinkcon", "fountain", "other"},
    "charges": {"wand", "staff"},
}


class ItemResourceError(ValueError):
    """A safe denial or quarantined resource requiring builder repair."""


def _integer(value: Any, low: int = 0, high: int = MAX_RESOURCE_UNITS) -> int:
    """Reject booleans and fractional, negative, or oversized resource values."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise ItemResourceError("Item resource values are out of bounds.")
    return value


def _identity(value: Any) -> str:
    """Accept bounded opaque service identities without executable content."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-zA-Z0-9_.:/-]{1,128}", value
    ):
        raise ItemResourceError("Invalid item resource identity.")
    return value


def validate_resource_profile(raw: Any, item_type: str | None = None) -> dict[str, Any]:
    """Validate authored units, compatibility, and exactly three recharge policies."""
    if not isinstance(raw, Mapping) or set(raw) != {
        "version",
        "kind",
        "resource_key",
        "current",
        "maximum",
        "recharge",
        "recharge_amount",
    }:
        raise ItemResourceError("Item resource profile has an invalid shape.")
    if raw["version"] != RESOURCE_VERSION or isinstance(raw["version"], bool):
        raise ItemResourceError("Item resource version is invalid.")
    kind = raw["kind"]
    if (
        not isinstance(kind, str)
        or kind not in _KIND_TYPES
        or (
            item_type is not None
            and (not isinstance(item_type, str) or item_type not in _KIND_TYPES[kind])
        )
    ):
        raise ItemResourceError("That item type cannot use this resource kind.")
    key = raw["resource_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", key):
        raise ItemResourceError("Resource compatibility key is invalid.")
    maximum = _integer(raw["maximum"])
    current = _integer(raw["current"], high=maximum)
    policy = raw["recharge"]
    if (
        policy not in ("none", "refill", "dawn")
        or (kind == "charges" and policy == "refill")
        or (kind != "charges" and policy == "dawn")
    ):
        raise ItemResourceError("Recharge policy is invalid for this resource.")
    amount = _integer(raw["recharge_amount"])
    if (policy == "dawn" and not 1 <= amount <= maximum) or (
        policy != "dawn" and amount != 0
    ):
        raise ItemResourceError("Recharge amount is invalid for this policy.")
    return {
        "version": RESOURCE_VERSION,
        "kind": kind,
        "resource_key": key,
        "current": current,
        "maximum": maximum,
        "recharge": policy,
        "recharge_amount": amount,
    }


def resource_profile(item: Any) -> dict[str, Any]:
    """Read a strict authored profile; malformed items fail independently."""
    return validate_resource_profile(
        item.attributes.get(RESOURCE_ATTRIBUTE), item.attributes.get("type") or "item"
    )


def resource_state(item: Any) -> dict[str, Any]:
    """Project durable units with maximum clamping and no resource creation."""
    profile = resource_profile(item)
    raw = item.attributes.get(RESOURCE_STATE_ATTRIBUTE)
    if raw is None:
        return {
            "version": RESOURCE_VERSION,
            "current": profile["current"],
            "lit": False,
            "lit_owner": None,
            "last_pulse": 0,
            "last_dawn": -1,
            "receipts": {},
        }
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {
            "version",
            "current",
            "lit",
            "lit_owner",
            "last_pulse",
            "last_dawn",
            "receipts",
        }
        or raw["version"] != RESOURCE_VERSION
        or isinstance(raw["version"], bool)
        or not isinstance(raw["lit"], bool)
    ):
        raise ItemResourceError("Item resource state needs builder repair.")
    state = dict(raw)
    state["current"] = min(_integer(raw["current"]), profile["maximum"])
    state["last_pulse"] = _integer(raw["last_pulse"], high=2_000_000_000)
    state["last_dawn"] = _integer(raw["last_dawn"], low=-1, high=2_000_000_000)
    if raw["lit_owner"] is not None:
        _integer(raw["lit_owner"], low=1, high=2_000_000_000)
    if raw["lit"] and (
        profile["kind"] != "fuel" or item.db.type != "light" or raw["lit_owner"] is None
    ):
        raise ItemResourceError("Item light state needs builder repair.")
    if not isinstance(raw["receipts"], Mapping):
        raise ItemResourceError("Item resource receipts need builder repair.")
    state["receipts"] = {}
    for key, receipt in raw["receipts"].items():
        _identity(key)
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != {"operation", "units", "peer"}
            or receipt["operation"]
            not in ("light", "extinguish", "refill", "source", "spend")
        ):
            raise ItemResourceError("Item resource receipts need builder repair.")
        _integer(receipt["units"])
        if receipt["peer"] is not None:
            _integer(receipt["peer"], low=1, high=2_000_000_000)
        state["receipts"][key] = dict(receipt)
    return state


def _refresh(obj: Any) -> None:
    """Forget Saver values and reload Attribute rows after lock/rollback."""
    obj.attributes.reset_cache()
    rows = {row["id"]: row for row in obj.db_attributes.values()}
    for attribute in obj.db_attributes.all():
        if attribute.pk in rows:
            attribute.__dict__.update(rows[attribute.pk])
    obj.attributes.reset_cache()


@contextmanager
def item_mutation(*objects: Any) -> Iterator[None]:
    """Serialize ORM writes and reject reentrant use of a reserved object.

    ITEM-04B shares this reservation lane so a potion, wand, or lamp can never
    be spent twice concurrently through two different item packages.
    """
    from evennia.objects.models import ObjectDB

    ids = {obj.pk for obj in objects if obj is not None}
    if any(
        not isinstance(key, int) or isinstance(key, bool) or key < 1 for key in ids
    ) or _ACTIVE.intersection(ids):
        raise ItemResourceError("That item is already in use or unavailable.")
    ids = sorted(ids)
    _ACTIVE.update(ids)
    try:
        with transaction.atomic():
            rows = list(
                ObjectDB.objects.select_for_update()
                .filter(pk__in=ids)
                .values("id", "db_location_id")
            )
            if len(rows) != len(ids):
                raise ItemResourceError("That item is unavailable.")
            for obj in objects:
                _refresh(obj)
                row = next(row for row in rows if row["id"] == obj.pk)
                if obj.db_location_id != row["db_location_id"]:
                    raise ItemResourceError("That item has moved.")
            yield
    except Exception:
        for obj in objects:
            _refresh(obj)
        raise
    finally:
        _ACTIVE.difference_update(ids)


def _store(item: Any, state: dict[str, Any]) -> None:
    """Persist detached state rather than mutating cached Saver containers."""
    item.attributes.add(RESOURCE_STATE_ATTRIBUTE, state)


def set_resource_profile(item: Any, raw: Any) -> None:
    """Author a live profile while preserving spent units and replay receipts."""
    profile = validate_resource_profile(raw, item.db.type or "item")
    with item_mutation(item):
        state = (
            resource_state(item) if item.attributes.has(RESOURCE_ATTRIBUTE) else None
        )
        if state is not None:
            prior = resource_profile(item)
            if (prior["kind"], prior["resource_key"]) != (
                profile["kind"],
                profile["resource_key"],
            ):
                raise ItemResourceError(
                    "Clear the resource before changing its compatibility kind."
                )
        item.attributes.add(RESOURCE_ATTRIBUTE, profile)
        if state is not None:
            state["current"] = min(state["current"], profile["maximum"])
            if not state["current"]:
                state["lit"] = False
                state["lit_owner"] = None
            _store(item, state)
        else:
            _store(item, resource_state(item))


def _usable(actor: Any, item: Any) -> None:
    """Require direct ownership, action policy, and observer access at commit."""
    decision = actor.actions.check(ActionCategory.MANIPULATE)
    if not decision.allowed:
        raise ItemResourceError(decision.message)
    if (
        item.location is not actor
        or actor.location is None
        or not item.access(actor, "view", default=True)
        or not item.access(actor, "interact", default=True)
    ):
        raise ItemResourceError("You must directly carry an accessible item.")


def _receipt(
    state: dict[str, Any], identity: str, operation: str, peer: int | None = None
) -> int | None:
    """Return a prior committed operation without replaying its output or spend."""
    receipt = state["receipts"].get(identity)
    if receipt is None:
        return None
    if receipt["operation"] != operation or receipt["peer"] != peer:
        raise ItemResourceError("That resource identity was already used.")
    return receipt["units"]


def _record(
    state: dict[str, Any],
    identity: str,
    operation: str,
    units: int,
    peer: int | None = None,
) -> None:
    """Retain durable receipts so late retries cannot manufacture units."""
    state["receipts"][identity] = {"operation": operation, "units": units, "peer": peer}


def _announce(actor: Any, item: Any, text: str, public_verb: str) -> None:
    """Queue one delivery attempt per committed transition, with isolated failures."""
    room = actor.location
    name = item.get_display_name(actor)

    def deliver() -> None:
        for callback in (
            lambda: actor.msg(text.format(name=name)),
            lambda: room.msg_contents(
                f"{actor.get_display_name(room)} {public_verb} {item.get_display_name(room)}.",
                exclude=actor,
            ),
        ):
            try:
                callback()
            except Exception:
                logger.log_err("Item resource message delivery failed after commit.")

    transaction.on_commit(deliver)


def set_light(actor: Any, item: Any, lit: bool, *, identity: str | None = None) -> bool:
    """Light/extinguish one carried lamp; receipts and state suppress duplicates."""
    if not isinstance(lit, bool):
        raise ItemResourceError("Invalid light transition.")
    identity = _identity(uuid4().hex if identity is None else identity)
    operation = "light" if lit else "extinguish"
    with item_mutation(actor, item):
        state = resource_state(item)
        if _receipt(state, identity, operation) is not None:
            return False
        _usable(actor, item)
        if item.db.type != "light" or resource_profile(item)["kind"] != "fuel":
            raise ItemResourceError("That item is not a usable light.")
        if state["lit"] == lit:
            raise ItemResourceError(
                "It is already lit." if lit else "It is already extinguished."
            )
        if lit and not state["current"]:
            raise ItemResourceError("That light has no fuel.")
        state["lit"] = lit
        state["lit_owner"] = actor.pk if lit else None
        _record(state, identity, operation, 0)
        _store(item, state)
        _announce(
            actor,
            item,
            "You light {name}." if lit else "You extinguish {name}.",
            "lights" if lit else "extinguishes",
        )
        return True


def refill_resource(
    actor: Any, target: Any, source: Any, *, identity: str | None = None
) -> int:
    """Move all whole compatible units that fit, without creating resources."""
    identity = _identity(uuid4().hex if identity is None else identity)
    if target is source:
        raise ItemResourceError("Choose two different items.")
    with item_mutation(actor, target, source):
        target_state, source_state = resource_state(target), resource_state(source)
        prior = _receipt(target_state, identity, "refill", source.pk)
        if prior is not None:
            if _receipt(source_state, identity, "source", target.pk) != prior:
                raise ItemResourceError("That refill receipt is incomplete.")
            return prior
        _usable(actor, target)
        _usable(actor, source)
        target_profile, source_profile = resource_profile(target), resource_profile(
            source
        )
        if (
            target_profile["recharge"] != "refill"
            or target_profile["kind"] == "charges"
            or source_profile["kind"] == "charges"
            or (target_profile["kind"], target_profile["resource_key"])
            != (source_profile["kind"], source_profile["resource_key"])
        ):
            raise ItemResourceError("Those resources cannot be refilled together.")
        if _receipt(source_state, identity, "source", target.pk) is not None:
            raise ItemResourceError("That refill receipt is incomplete.")
        units = min(
            target_profile["maximum"] - target_state["current"], source_state["current"]
        )
        if not units:
            raise ItemResourceError("The source is empty or the target is full.")
        target_state["current"] += units
        source_state["current"] -= units
        _record(target_state, identity, "refill", units, source.pk)
        _record(source_state, identity, "source", units, target.pk)
        _store(target, target_state)
        _store(source, source_state)
        if source_state["lit"] and not source_state["current"]:
            _extinguish(source, source_state, actor)
        _announce(actor, target, f"You refill {{name}} with {units} units.", "refills")
        return units


def commit_resource_use(
    item: Any,
    units: int,
    identity: str,
    adapter: Callable[[], bool],
    *,
    participants: tuple[Any, ...] = (),
) -> bool:
    """ITEM-04's reservation seam: spend only if the owning adapter commits.

    The caller's magic/consumable adapter validates actor/target access and owns
    effects and messages. All its ORM changes share this rollback transaction.
    Adapter exceptions and a false return release the item reservation. Supply
    actor/target in participants so their Attribute caches are refreshed after
    rollback as well; adapters must queue their output with on_commit.
    """
    units = _integer(units, low=1)
    identity = _identity(identity)
    with item_mutation(item, *participants):
        state = resource_state(item)
        prior = _receipt(state, identity, "spend")
        if prior is not None:
            if prior != units:
                raise ItemResourceError("That use identity has different units.")
            return True
        if state["current"] < units:
            raise ItemResourceError("That item has insufficient units.")
        with transaction.atomic():
            if adapter() is not True:
                transaction.set_rollback(True)
            else:
                state["current"] -= units
                _record(state, identity, "spend", units)
                _store(item, state)
                if state["lit"] and not state["current"]:
                    _extinguish(item, state, item.location)
                return True
        for participant in (item, *participants):
            _refresh(participant)
        return False


def _extinguish(item: Any, state: dict[str, Any], carrier: Any | None) -> None:
    """Persist extinction before attempting its one exhaustion/move message."""
    if not state["lit"]:
        return
    state["lit"] = False
    state["lit_owner"] = None
    _store(item, state)
    if carrier is not None and getattr(carrier, "location", None) is not None:
        _announce(carrier, item, "{name} goes out.", "sees the light go out on")


def extinguish_moved_light(item: Any, former_carrier: Any | None) -> None:
    """Clear active illumination immediately after an item changes ownership."""
    if not item.attributes.has(RESOURCE_ATTRIBUTE):
        return
    try:
        with item_mutation(item):
            state = resource_state(item)
            _extinguish(item, state, former_carrier)
    except ItemResourceError:
        # Invalid resources contribute no light and cannot poison item movement.
        return


def active_light_level(observer: Any, room: Any) -> Any:
    """Supply Bright light from directly carried lamps in this one room."""
    from systems.room_environment import LightLevel
    from typeclasses.characters import Character

    for carrier in room.contents:
        if not isinstance(carrier, Character):
            continue
        for item in carrier.contents:
            if item.db.type != "light" or not item.attributes.has(RESOURCE_ATTRIBUTE):
                continue
            try:
                state = resource_state(item)
            except ItemResourceError:
                continue
            if (
                state["lit"]
                and state["current"] > 0
                and state["lit_owner"] == carrier.pk
            ):
                return LightLevel.BRIGHT
    return None


def process_object_pulse(event: Any) -> None:
    """Consume one unit per fresh objects token, isolating malformed owners."""
    from evennia.objects.models import ObjectDB
    from systems.pulses import PulseLane
    from typeclasses.characters import Character

    if event.lane != PulseLane.OBJECTS:
        raise ItemResourceError("Invalid objects pulse lane.")
    token = _integer(event.sequence, low=1, high=2_000_000_000)
    for item in (
        ObjectDB.objects.filter(db_attributes__db_key=RESOURCE_ATTRIBUTE)
        .distinct()
        .iterator()
    ):
        try:
            with item_mutation(item):
                state = resource_state(item)
                if token <= state["last_pulse"]:
                    continue
                state["last_pulse"] = token
                carrier = item.location
                if state["lit"]:
                    if (
                        not isinstance(carrier, Character)
                        or state["lit_owner"] != carrier.pk
                        or carrier.location is None
                        or state["current"] == 0
                    ):
                        _extinguish(
                            item,
                            state,
                            carrier if isinstance(carrier, Character) else None,
                        )
                    else:
                        state["current"] -= 1
                        if not state["current"]:
                            _extinguish(item, state, carrier)
                _store(item, state)
        except Exception:
            logger.log_trace(
                f"Objects pulse isolated an invalid resource on object #{item.pk}."
            )


def process_dawn_recharge(boundary: Any) -> None:
    """Restore the authored amount once per fresh dawn day, never missed days."""
    from evennia.objects.models import ObjectDB

    if boundary.kind != "dawn":
        return
    day = _integer(boundary.day, high=2_000_000_000)
    for item in (
        ObjectDB.objects.filter(db_attributes__db_key=RESOURCE_ATTRIBUTE)
        .distinct()
        .iterator()
    ):
        try:
            with item_mutation(item):
                profile, state = resource_profile(item), resource_state(item)
                if profile["recharge"] != "dawn" or day <= state["last_dawn"]:
                    continue
                state["current"] = min(
                    profile["maximum"], state["current"] + profile["recharge_amount"]
                )
                state["last_dawn"] = day
                _store(item, state)
        except Exception:
            logger.log_trace(
                f"Dawn recharge isolated an invalid resource on object #{item.pk}."
            )


def register_resource_clock_consumer() -> None:
    """Install the dawn adapter at startup as well as before every clock pulse."""
    from systems.world_clock import register_clock_consumer

    register_clock_consumer(
        "item_resources", frozenset({"dawn"}), process_dawn_recharge
    )

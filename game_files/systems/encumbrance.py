"""Canonical carrying-load calculation and admission policy.

All gameplay placement into a character or container goes through this module.
Weights are stored and compared as integer hundredths of a pound, avoiding
surprising boundary failures from binary floating-point values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import isfinite
from numbers import Real
from typing import Any, Sequence

from django.conf import settings
from evennia.prototypes.spawner import spawn
from evennia.utils.logger import log_info

HUNDREDTHS_PER_POUND = 100
DEFAULT_CARRIED_ITEM_LIMIT = 100
DEFAULT_MAX_CONTAINER_DEPTH = 20


class EncumbranceError(ValueError):
    """Raised when persisted load data cannot be evaluated safely."""


@dataclass(frozen=True)
class SubtreeLoad:
    """The recursive object count and normalized weight for one subtree."""

    count: int
    weight_units: int

    @property
    def weight(self) -> float:
        """Return the normalized weight in player-facing pounds."""
        return self.weight_units / HUNDREDTHS_PER_POUND


@dataclass(frozen=True)
class LoadSummary:
    """A character's present carried load and effective limits."""

    count: int
    weight_units: int
    count_limit: int
    weight_limit_units: int
    error: str | None = None

    @property
    def weight(self) -> float:
        """Return carried weight in player-facing pounds."""
        return self.weight_units / HUNDREDTHS_PER_POUND

    @property
    def weight_limit(self) -> float:
        """Return carrying capacity in player-facing pounds."""
        return self.weight_limit_units / HUNDREDTHS_PER_POUND

    @property
    def overloaded(self) -> bool:
        """Whether this load cannot accept an additional carried object."""
        return bool(
            self.error
            or self.count > self.count_limit
            or self.weight_units > self.weight_limit_units
        )


@dataclass(frozen=True)
class AdmissionResult:
    """The outcome of checking an aggregate transfer before it is moved."""

    allowed: bool
    reason: str | None = None
    message: str | None = None
    subtree: SubtreeLoad | None = None
    destination_load: LoadSummary | None = None


def pounds_to_units(value: Any, *, label: str = "weight") -> int:
    """Validate pounds and normalize them to integer hundredths.

    ``bool`` is deliberately excluded even though it is an ``int`` subclass.
    Builder and prototype validation both use this function, so invalid values
    cannot enter new content through one path but not the other.
    """
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal, str)):
        raise EncumbranceError(f"{label} must be a finite number of pounds.")
    if isinstance(value, float) and not isfinite(value):
        raise EncumbranceError(f"{label} must be a finite number of pounds.")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EncumbranceError(f"{label} must be a finite number of pounds.") from None
    if not decimal.is_finite() or decimal < 0:
        raise EncumbranceError(f"{label} cannot be negative or non-finite.")
    return int((decimal * HUNDREDTHS_PER_POUND).quantize(Decimal("1"), ROUND_HALF_UP))


def units_to_pounds(units: int) -> float:
    """Convert normalized units to a compact player-facing number."""
    return units / HUNDREDTHS_PER_POUND


def _setting_int(name: str, default: int) -> int:
    """Read a non-negative integer setting, failing safely to its default."""
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def carried_item_limit(owner: Any) -> int:
    """Return an explicit owner override or the settings-managed safe limit."""
    override = owner.attributes.get("carry_item_limit")
    if override is None:
        return _setting_int("CARRIED_ITEM_LIMIT", DEFAULT_CARRIED_ITEM_LIMIT)
    if isinstance(override, bool) or not isinstance(override, int) or override < 0:
        raise EncumbranceError("carry item limit must be a non-negative whole number.")
    return override


def _is_character(obj: Any) -> bool:
    """Identify PC and NPC carriers without coupling this system to a typeclass."""
    return hasattr(obj, "stats") and hasattr(obj, "contents")


def is_container(obj: Any) -> bool:
    """Return whether an object uses the data-driven container item type."""
    return str(obj.attributes.get("type") or "").lower() == "container"


def object_weight_units(obj: Any) -> int:
    """Return an item's own normalized weight; absent legacy data means zero."""
    value = obj.attributes.get("weight")
    if value is None:
        return 0
    return pounds_to_units(value, label=f"weight on {obj.key}")


def subtree_load(root: Any) -> SubtreeLoad:
    """Recursively total an object and all descendants, rejecting bad trees."""
    max_depth = _setting_int("MAX_CONTAINER_NESTING", DEFAULT_MAX_CONTAINER_DEPTH)
    visited: set[int] = set()

    def visit(node: Any, depth: int) -> SubtreeLoad:
        if depth > max_depth:
            raise EncumbranceError(
                f"container nesting exceeds the configured limit of {max_depth}."
            )
        node_id = getattr(node, "id", None)
        marker = int(node_id) if node_id is not None else id(node)
        if marker in visited:
            raise EncumbranceError("containment cycle detected.")
        visited.add(marker)
        count = 1
        weight = object_weight_units(node)
        for child in node.contents:
            child_load = visit(child, depth + 1)
            count += child_load.count
            weight += child_load.weight_units
        return SubtreeLoad(count=count, weight_units=weight)

    return visit(root, 0)


def character_load(owner: Any) -> LoadSummary:
    """Calculate a PC or NPC's recursive carried count, weight, and limits."""
    try:
        count = 0
        weight = 0
        for item in owner.contents:
            item_load = subtree_load(item)
            count += item_load.count
            weight += item_load.weight_units
        count_limit = carried_item_limit(owner)
        weight_limit = pounds_to_units(
            owner.stats.carry_capacity, label="carry capacity"
        )
        return LoadSummary(count, weight, count_limit, weight_limit)
    except (AttributeError, EncumbranceError) as err:
        return LoadSummary(0, 0, 0, 0, str(err))


def _ancestors(obj: Any) -> list[Any]:
    """Return an object's location chain while detecting pre-existing cycles."""
    result: list[Any] = []
    seen: set[int] = set()
    current = obj
    while current is not None:
        marker = (
            int(current.id) if getattr(current, "id", None) is not None else id(current)
        )
        if marker in seen:
            raise EncumbranceError("containment cycle detected.")
        seen.add(marker)
        result.append(current)
        current = getattr(current, "location", None)
    return result


def carrier_for(location: Any) -> Any | None:
    """Find the carrying character that owns a location, if it has one."""
    for ancestor in _ancestors(location):
        if _is_character(ancestor):
            return ancestor
    return None


def _container_ancestors(location: Any) -> list[Any]:
    """Return every destination container whose capacity is affected."""
    return [ancestor for ancestor in _ancestors(location) if is_container(ancestor)]


def container_capacity_units(container: Any) -> int:
    """Return a configured container capacity or reject player insertion."""
    if not is_container(container):
        raise EncumbranceError("destination is not a container.")
    value = container.attributes.get("capacity")
    if value is None:
        raise EncumbranceError(f"{container.key} has no configured capacity.")
    return pounds_to_units(value, label=f"capacity on {container.key}")


def _same_object(left: Any, right: Any) -> bool:
    """Compare persistent objects safely, including unpersisted test doubles."""
    return left is right or (
        getattr(left, "id", None) is not None
        and getattr(left, "id", None) == getattr(right, "id", None)
    )


def _message(reason: str, destination: Any) -> str:
    """Map stable denial reasons to one player-safe diagnostic."""
    messages = {
        "invalid_tree": "That transfer cannot be completed because its contents are invalid.",
        "containment_cycle": "You cannot put an item inside itself.",
        "character_count_limit": "You cannot carry any more items.",
        "character_weight_limit": "That would exceed your carrying capacity.",
        "container_capacity": f"That would exceed {destination.key}'s capacity.",
        "container_unconfigured": f"{destination.key} is not configured to hold items.",
    }
    return messages[reason]


def can_receive(destination: Any, arriving: Sequence[Any] | Any) -> AdmissionResult:
    """Preflight an aggregate transfer into a character or container.

    The result is deliberately independent of command parsing.  Commands,
    direct ``move_to`` hooks, reward placement, and future ``put`` all use the
    same decision and stable denial reason.
    """
    arrivals = tuple(
        arriving if isinstance(arriving, (list, tuple, set)) else (arriving,)
    )
    if not arrivals:
        return AdmissionResult(True, subtree=SubtreeLoad(0, 0))
    try:
        destination_chain = _ancestors(destination)
        unique: list[Any] = []
        for item in arrivals:
            if any(_same_object(item, existing) for existing in unique):
                continue
            if any(_same_object(item, ancestor) for ancestor in destination_chain):
                return AdmissionResult(
                    False,
                    "containment_cycle",
                    _message("containment_cycle", destination),
                )
            unique.append(item)
        # A batch that names a container and one of its children is ambiguous;
        # reject it rather than count the child twice or move it independently.
        for item in unique:
            for other in unique:
                if item is not other and any(
                    _same_object(other, ancestor)
                    for ancestor in _ancestors(item.location)
                ):
                    return AdmissionResult(
                        False, "invalid_tree", _message("invalid_tree", destination)
                    )

        loads = {item: subtree_load(item) for item in unique}
        total = SubtreeLoad(
            sum(load.count for load in loads.values()),
            sum(load.weight_units for load in loads.values()),
        )

        destination_carrier = carrier_for(destination)
        summary = character_load(destination_carrier) if destination_carrier else None
        if summary and summary.error:
            return AdmissionResult(
                False,
                "invalid_tree",
                _message("invalid_tree", destination),
                total,
                summary,
            )
        if destination_carrier:
            added = [
                item
                for item in unique
                if not _same_object(carrier_for(item.location), destination_carrier)
            ]
            added_load = SubtreeLoad(
                sum(loads[item].count for item in added),
                sum(loads[item].weight_units for item in added),
            )
            if summary.count + added_load.count > summary.count_limit:
                return AdmissionResult(
                    False,
                    "character_count_limit",
                    _message("character_count_limit", destination),
                    total,
                    summary,
                )
            if (
                summary.weight_units + added_load.weight_units
                > summary.weight_limit_units
            ):
                return AdmissionResult(
                    False,
                    "character_weight_limit",
                    _message("character_weight_limit", destination),
                    total,
                    summary,
                )

        # Containers use total descendant weight.  For moves within one carried
        # tree, subtracting source ancestry before adding destination ancestry
        # leaves shared ancestors unchanged while still checking the new branch.
        # Only the destination ancestry can become more full.  Source
        # containers are deliberately not validated: removing an item is a
        # load-reducing action and must remain available to repair old data.
        affected = _container_ancestors(destination)
        distinct: list[Any] = []
        for container in affected:
            if not any(_same_object(container, known) for known in distinct):
                distinct.append(container)
        for container in distinct:
            current = subtree_load(container).weight_units - object_weight_units(
                container
            )
            delta = 0
            for item, item_load in loads.items():
                if any(
                    _same_object(container, parent)
                    for parent in _container_ancestors(destination)
                ):
                    delta += item_load.weight_units
                if any(
                    _same_object(container, parent)
                    for parent in _container_ancestors(item.location)
                ):
                    delta -= item_load.weight_units
            try:
                capacity = container_capacity_units(container)
            except EncumbranceError:
                return AdmissionResult(
                    False,
                    "container_unconfigured",
                    _message("container_unconfigured", container),
                    total,
                    summary,
                )
            if current + delta > capacity:
                return AdmissionResult(
                    False,
                    "container_capacity",
                    _message("container_capacity", container),
                    total,
                    summary,
                )
        return AdmissionResult(True, subtree=total, destination_load=summary)
    except EncumbranceError:
        return AdmissionResult(
            False, "invalid_tree", _message("invalid_tree", destination)
        )


def place_with_capacity(
    item: Any,
    destination: Any,
    *,
    bypass_reason: str | None = None,
    move_type: str = "placement",
) -> bool:
    """Place one existing item through admission, with an explicit bypass.

    Forced system operations must name why they bypass normal capacity.  The
    receiving hook logs that reason, leaving an auditable trail for staff.
    """
    kwargs: dict[str, Any] = {}
    if bypass_reason is not None:
        if not isinstance(bypass_reason, str) or not bypass_reason.strip():
            raise ValueError("A capacity bypass requires a non-empty reason.")
        kwargs["encumbrance_bypass"] = bypass_reason.strip()
    else:
        result = can_receive(destination, item)
        if not result.allowed:
            return False
    return bool(item.move_to(destination, quiet=True, move_type=move_type, **kwargs))


def spawn_with_capacity(
    prototype: dict[str, Any], destination: Any, *, bypass_reason: str | None = None
) -> list[Any]:
    """Spawn a prototype and place all results through the capacity policy."""
    for key in ("weight", "capacity"):
        if key in prototype:
            pounds_to_units(prototype[key], label=key)
    spawned = list(
        spawn({key: value for key, value in prototype.items() if key != "location"})
    )
    result = can_receive(destination, spawned)
    if not result.allowed and bypass_reason is None:
        for item in spawned:
            item.delete()
        return []
    for item in spawned:
        if not place_with_capacity(item, destination, bypass_reason=bypass_reason):
            # These objects were created solely for this failed transaction;
            # remove every result so a partial reward never leaves an orphan.
            for spawned_item in spawned:
                spawned_item.delete()
            return []
    return spawned


def audit_bypass(item: Any, destination: Any, reason: str) -> None:
    """Record a deliberate forced capacity bypass for later staff review."""
    log_info(
        "Encumbrance bypass: {item} -> {destination} ({reason})",
        item=getattr(item, "dbref", item),
        destination=getattr(destination, "dbref", destination),
        reason=reason,
    )

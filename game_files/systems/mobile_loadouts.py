"""AREA-05D validated, all-or-nothing NPC prototype loadouts."""

from __future__ import annotations

from typing import Any, Mapping

from systems.prototype_catalogs import PrototypeCatalogError, resolve_prototype

MAX_LOADOUT_ITEMS = 100


class MobileLoadoutError(ValueError):
    """Raised when an authored NPC loadout cannot safely materialize."""


def validate_loadout(raw: Any) -> tuple[dict[str, Any], ...]:
    """Validate a bounded flat source-item loadout in deterministic order."""
    if raw in (None, {}):
        return ()
    if not isinstance(raw, Mapping) or set(raw) != {"version", "items"}:
        raise MobileLoadoutError("NPC loadout must contain version and items.")
    if raw["version"] != 1 or not isinstance(raw["items"], list):
        raise MobileLoadoutError("NPC loadout has an invalid version or item list.")
    total = 0
    validated: list[dict[str, Any]] = []
    for node in raw["items"]:
        if not isinstance(node, Mapping) or set(node) - {"prototype_key", "quantity", "wear"}:
            raise MobileLoadoutError("NPC loadout item has an invalid shape.")
        key, quantity = node.get("prototype_key"), node.get("quantity")
        if not isinstance(key, str) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise MobileLoadoutError("NPC loadout item needs a key and positive quantity.")
        try:
            prototype = resolve_prototype(key, kind="item")
        except PrototypeCatalogError as err:
            raise MobileLoadoutError("NPC loadout item is not source-owned.") from err
        if prototype.get("type") == "money" or prototype.get("account_bound"):
            raise MobileLoadoutError("NPC loadout cannot use money or bound items.")
        wear = node.get("wear")
        if wear is not None and (not isinstance(wear, str) or quantity != 1):
            raise MobileLoadoutError("Only one item may occupy one wear location.")
        total += quantity
        if total > MAX_LOADOUT_ITEMS:
            raise MobileLoadoutError("NPC loadout has too many items.")
        validated.append({"prototype_key": key, "quantity": quantity, "wear": wear})
    return tuple(validated)


def materialize_loadout(npc: Any, raw: Any) -> tuple[Any, ...]:
    """Create/equip a validated loadout, deleting every new item on failure."""
    entries = validate_loadout(raw)
    if not entries:
        return ()
    from systems.encumbrance import spawn_with_capacity

    created: list[Any] = []
    try:
        for entry in entries:
            prototype = resolve_prototype(entry["prototype_key"], kind="item")
            for index in range(entry["quantity"]):
                spawned = spawn_with_capacity(prototype, npc)
                if len(spawned) != 1:
                    raise MobileLoadoutError("NPC cannot carry its authored loadout.")
                item = spawned[0]
                created.append(item)
                if entry["wear"] is not None and index == 0:
                    npc.equipment.equip(item, entry["wear"])
    except Exception as err:
        for item in reversed(created):
            if getattr(item, "pk", None):
                item.delete()
        if isinstance(err, MobileLoadoutError):
            raise
        raise MobileLoadoutError("NPC loadout could not be materialized.") from err
    return tuple(created)

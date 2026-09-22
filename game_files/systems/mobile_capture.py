"""AREA-05C safe live-NPC capture and restricted Builder force policy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from evennia.utils import logger
from systems.mob_spawning import mobile_spawn_identity
from systems.prototype_catalogs import PrototypeCatalogError, resolve_prototype

FORCE_ALLOWLIST = frozenset({"inventory", "get", "put", "drop", "wear", "remove"})


class MobileCaptureError(ValueError):
    """Raised when a live NPC operation is unsafe or outside its scope."""


def _source_key(npc: Any) -> str:
    if getattr(getattr(npc, "db", None), "is_player_character", None) is not False:
        raise MobileCaptureError("Only a live NPC can be captured.")
    identity = mobile_spawn_identity(npc)
    if identity is None:
        raise MobileCaptureError("That NPC has no MOB-05 source identity.")
    try:
        resolve_prototype(identity.prototype_key, kind="mobile")
    except PrototypeCatalogError as err:
        raise MobileCaptureError("That NPC is not linked to one source template.") from err
    return identity.prototype_key


def capture_npc(npc: Any) -> tuple[str, dict[str, Any]]:
    """Extract only builder-schema fields from a source-linked live NPC."""
    key = _source_key(npc)
    from systems.mobile_policy import mobile_policy
    from world.build_schema import schema_for_prototype

    source = resolve_prototype(key, kind="mobile")
    schema = schema_for_prototype(source) or {}
    captured = deepcopy(source)
    policy = mobile_policy(npc)
    for name, field in schema.items():
        target = field.target or name
        if field.kind == "key":
            captured["key"] = npc.key
        elif field.kind == "attr" and npc.attributes.has(target):
            captured[target] = deepcopy(npc.attributes.get(target))
        elif field.kind == "policy" and target in policy:
            captured[target] = deepcopy(policy[target])
    # hp_current and xp are authored fields; the loop above intentionally takes
    # their displayed stored values rather than deriving them from combat state.
    return key, captured


def force_npc(npc: Any, command: str, actor: Any) -> None:
    """Run one narrow loadout command through the NPC's normal command path."""
    _source_key(npc)
    raw = command.strip()
    if not raw or len(raw) > 240 or any(mark in raw for mark in (";", "\n", "\r")):
        raise MobileCaptureError("Force accepts one loadout command only.")
    verb = raw.split(maxsplit=1)[0].casefold()
    if verb not in FORCE_ALLOWLIST:
        raise MobileCaptureError("That command is not allowed for NPC loadouts.")
    logger.log_info(
        f"AREA-05C force builder={getattr(actor, 'id', None)} "
        f"npc={getattr(npc, 'id', None)} command={verb}"
    )
    npc.execute_cmd(raw)

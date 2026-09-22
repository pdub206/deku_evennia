"""AREA-05A source-owned, deterministic release prototype catalogs.

The release catalog is deliberately read from the tracked ``world.prototypes``
module, never from the prototype database.  Database prototypes are therefore
useful Builder drafts/legacy diagnostics, but cannot silently replace content
named by a release manifest.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from evennia.prototypes.prototypes import search_prototype
from world.build_schema import as_slug

CATALOG_VERSION = 1
ITEM_TYPECLASS = "typeclasses.objects.Item"
NPC_TYPECLASS = "typeclasses.characters.Character"


class PrototypeCatalogError(ValueError):
    """Raised when source catalog content is invalid or obscured."""


@dataclass(frozen=True)
class PrototypeCatalog:
    """An immutable, globally-keyed projection of the release catalog."""

    version: int
    fingerprint: str
    records: Mapping[str, Mapping[str, Any]]


def _literal(value: Any) -> bool:
    """Return whether a prototype contains only deterministic data values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _literal(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return all(_literal(item) for item in value)
    return False


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


def _source_records() -> dict[str, dict[str, Any]]:
    """Read every authored release record without consulting database state."""
    from world import prototypes

    records: dict[str, dict[str, Any]] = {}
    for name, value in vars(prototypes).items():
        if not isinstance(value, Mapping) or "prototype_key" not in value:
            continue
        key = value.get("prototype_key")
        if not isinstance(key, str):
            raise PrototypeCatalogError(f"Prototype {name} has an invalid key.")
        try:
            key = as_slug(key)
        except ValueError as err:
            raise PrototypeCatalogError(
                f"Prototype {name} has an invalid key."
            ) from err
        if key != name or key in records:
            raise PrototypeCatalogError(
                f"Prototype key '{key}' is not globally unique."
            )
        record = dict(value)
        if not _literal(record):
            raise PrototypeCatalogError(f"Prototype '{key}' contains executable data.")
        if record.get("typeclass") not in {ITEM_TYPECLASS, NPC_TYPECLASS}:
            raise PrototypeCatalogError(
                f"Prototype '{key}' has an unsupported typeclass."
            )
        if not isinstance(record.get("key"), str) or not record["key"].strip():
            raise PrototypeCatalogError(f"Prototype '{key}' needs a name.")
        if (
            not isinstance(record.get("srd_reference"), str)
            or not record["srd_reference"].strip()
        ):
            raise PrototypeCatalogError(
                f"Prototype '{key}' needs an SRD/adaptation reference."
            )
        if record["typeclass"] == ITEM_TYPECLASS:
            # Reuse ITEM-07A's flattening/inheritance and Builder-schema
            # validation. The synthetic module tag enforces source ownership.
            from systems.starting_packages import (
                StartingPackageError,
                resolve_item_facts,
            )

            candidate = {**record, "prototype_tags": ["module"]}
            try:
                resolve_item_facts(key, [candidate])
            except StartingPackageError as err:
                raise PrototypeCatalogError(
                    f"Prototype '{key}' is invalid: {err}"
                ) from err
        records[key] = record
    return records


def build_catalog() -> PrototypeCatalog:
    """Validate and fingerprint the complete deterministic source catalog."""
    records = _source_records()
    if not records:
        raise PrototypeCatalogError("The release prototype catalog is empty.")
    payload = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return PrototypeCatalog(
        CATALOG_VERSION,
        sha256(payload.encode("utf-8")).hexdigest(),
        MappingProxyType({key: _freeze(records[key]) for key in sorted(records)}),
    )


def resolve_prototype(key: object, *, kind: str | None = None) -> dict[str, Any]:
    """Resolve one release prototype and reject an exact database shadow.

    ``kind`` is ``item`` or ``mobile`` when the caller has a required kind.
    The returned dict is a private mutable spawn payload.
    """
    if not isinstance(key, str):
        raise PrototypeCatalogError("Prototype key is invalid.")
    try:
        stable_key = as_slug(key)
    except ValueError as err:
        raise PrototypeCatalogError("Prototype key is invalid.") from err
    catalog = build_catalog()
    try:
        record = catalog.records[stable_key]
    except KeyError as err:
        raise PrototypeCatalogError(
            f"Unknown release prototype '{stable_key}'."
        ) from err
    expected = {"item": ITEM_TYPECLASS, "mobile": NPC_TYPECLASS}.get(kind)
    if kind is not None and expected is None:
        raise PrototypeCatalogError("Prototype kind is invalid.")
    if expected is not None and record["typeclass"] != expected:
        raise PrototypeCatalogError(
            f"Release prototype '{stable_key}' is the wrong kind."
        )
    # ``search_prototype`` includes the module record.  A second exact result is
    # a database shadow and must be repaired rather than selected by precedence.
    matches = [
        item
        for item in search_prototype(stable_key)
        if item.get("prototype_key") == stable_key
    ]
    if len(matches) != 1:
        raise PrototypeCatalogError(
            f"Release prototype '{stable_key}' is shadowed or unregistered."
        )
    return _thaw(record)


def resolve_runtime_prototype(
    key: object, *, kind: str | None = None
) -> dict[str, Any]:
    """Resolve source content, or one legacy record outside a release plan.

    AREA planning must call :func:`resolve_prototype`; this compatibility path
    lets existing Builder-created worlds continue to run until AREA-05B exports
    them. It still refuses ambiguity and never lets a legacy record shadow a
    released key.
    """
    try:
        return resolve_prototype(key, kind=kind)
    except PrototypeCatalogError as source_error:
        if "Unknown release prototype" not in str(source_error):
            raise
    if not isinstance(key, str):
        raise PrototypeCatalogError("Prototype key is invalid.")
    stable_key = as_slug(key)
    expected = {"item": ITEM_TYPECLASS, "mobile": NPC_TYPECLASS}.get(kind)
    if kind is not None and expected is None:
        raise PrototypeCatalogError("Prototype kind is invalid.")
    matches = [
        item
        for item in search_prototype(stable_key)
        if item.get("prototype_key") == stable_key
    ]
    if len(matches) != 1 or (
        expected is not None and matches[0].get("typeclass") != expected
    ):
        raise PrototypeCatalogError(
            f"Runtime prototype '{stable_key}' is missing or ambiguous."
        )
    return deepcopy(matches[0])


def catalog_for_area_planning() -> dict[str, dict[str, dict[str, Any]]]:
    """Return the data-only catalog shape consumed by AREA-02 planning."""
    catalog = build_catalog()
    items: dict[str, dict[str, Any]] = {}
    mobiles: dict[str, dict[str, Any]] = {}
    for key, record in catalog.records.items():
        target = items if record["typeclass"] == ITEM_TYPECLASS else mobiles
        target[key] = _thaw(record)
    return {"items": items, "mobiles": mobiles}

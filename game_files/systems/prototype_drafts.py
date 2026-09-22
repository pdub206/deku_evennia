"""AREA-05B versioned Builder drafts and deterministic catalog export."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from pprint import pformat
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from evennia.server.models import ServerConfig
from systems.prototype_catalogs import ITEM_TYPECLASS, build_catalog

_CONFIG_KEY = "area05b_prototype_drafts"
_DRAFT_MARKER = "_area05b_draft"


class PrototypeDraftError(ValueError):
    """Raised when a draft is stale, invalid, or cannot be exported."""


def _plain(value: Any) -> Any:
    """Copy immutable catalog values into Attribute-safe mutable data."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    return deepcopy(value)


def _store() -> dict[str, dict[str, Any]]:
    raw = ServerConfig.objects.conf(_CONFIG_KEY, default={})
    if not isinstance(raw, Mapping):
        raise PrototypeDraftError("Prototype draft storage needs staff repair.")
    return deepcopy(dict(raw))


def _save(store: dict[str, dict[str, Any]]) -> None:
    ServerConfig.objects.conf(_CONFIG_KEY, store)


def begin_draft(key: str, actor: Any) -> dict[str, Any]:
    """Return a mutable source-template draft tied to its catalog revision."""
    catalog = build_catalog()
    if key not in catalog.records:
        raise PrototypeDraftError("That prototype is not source-owned.")
    store = _store()
    existing = store.get(key)
    if existing is not None:
        if existing.get("base_fingerprint") != catalog.fingerprint:
            raise PrototypeDraftError("That draft has a stale source base.")
        record = deepcopy(existing["record"])
    else:
        record = {name: _plain(value) for name, value in catalog.records[key].items()}
        store[key] = {
            "version": 1,
            "base_fingerprint": catalog.fingerprint,
            "builder_id": getattr(actor, "id", None),
            "record": record,
        }
        _save(store)
    record[_DRAFT_MARKER] = key
    return record


def save_draft(target: Mapping[str, Any]) -> None:
    """Persist one editor-mutated draft without creating a shadow prototype."""
    key = target.get(_DRAFT_MARKER)
    if not isinstance(key, str):
        raise PrototypeDraftError("This is not a source-template draft.")
    catalog = build_catalog()
    store = _store()
    draft = store.get(key)
    if draft is None or draft.get("base_fingerprint") != catalog.fingerprint:
        raise PrototypeDraftError("That draft has a stale source base.")
    record = {name: deepcopy(value) for name, value in target.items() if name != _DRAFT_MARKER}
    if record.get("prototype_key") != key or record.get("typeclass") not in {
        ITEM_TYPECLASS,
        "typeclasses.characters.Character",
    }:
        raise PrototypeDraftError("Draft identity cannot be changed.")
    draft["record"] = record
    store[key] = draft
    _save(store)


def draft_diff(key: str) -> list[str]:
    """Return a bounded stable field diff for a source-owned draft."""
    catalog = build_catalog()
    draft = _store().get(key)
    if draft is None:
        return []
    if draft.get("base_fingerprint") != catalog.fingerprint:
        raise PrototypeDraftError("That draft has a stale source base.")
    before, after = catalog.records[key], draft["record"]
    names = sorted(set(before) | set(after))
    lines = [name for name in names if before.get(name) != after.get(name)]
    return lines[:50]


def _validate_record(key: str, record: Mapping[str, Any]) -> None:
    if record.get("prototype_key") != key:
        raise PrototypeDraftError("Draft prototype key is invalid.")
    if record.get("typeclass") == ITEM_TYPECLASS:
        from systems.starting_packages import StartingPackageError, resolve_item_facts

        try:
            resolve_item_facts(key, [{**record, "prototype_tags": ["module"]}])
        except StartingPackageError as err:
            raise PrototypeDraftError(f"Draft '{key}' is invalid: {err}") from err


def export_drafts(path: Path) -> tuple[str, ...]:
    """Atomically render all source records plus valid drafts in stable order."""
    catalog = build_catalog()
    store = _store()
    records = {key: _plain(value) for key, value in catalog.records.items()}
    changed: list[str] = []
    for key in sorted(store):
        draft = store[key]
        if draft.get("base_fingerprint") != catalog.fingerprint:
            raise PrototypeDraftError("A draft has a stale source base.")
        if key not in records or not isinstance(draft.get("record"), Mapping):
            raise PrototypeDraftError("A draft is malformed.")
        _validate_record(key, draft["record"])
        records[key] = deepcopy(draft["record"])
        changed.append(key)
    lines = [
        '"""Generated AREA-05B release prototype catalog; edit through Builder drafts."""',
        "",
    ]
    for key in sorted(records):
        lines.extend((f"{key} = {pformat(records[key], sort_dicts=True, width=88)}", ""))
    content = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)
    return tuple(changed)


def draft_target(target: Any) -> bool:
    """Whether an editor target is one AREA-05B in-memory source draft."""
    return isinstance(target, Mapping) and isinstance(target.get(_DRAFT_MARKER), str)


def draft_status() -> dict[str, list[str]]:
    """Summarize drafts for Builder diagnostics without exposing stored values."""
    catalog = build_catalog()
    store = _store()
    incorporated = [
        key
        for key, draft in store.items()
        if key in catalog.records
        and draft.get("base_fingerprint") != catalog.fingerprint
        and draft.get("record") == _plain(catalog.records[key])
    ]
    for key in incorporated:
        del store[key]
    if incorporated:
        _save(store)
    status: dict[str, list[str]] = {}
    for key in sorted(store):
        draft = store[key]
        if draft.get("base_fingerprint") != catalog.fingerprint:
            status[key] = ["stale source base"]
        else:
            status[key] = draft_diff(key)
    return status

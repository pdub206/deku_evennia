"""Frozen, source-controlled P04-A01 census of currently authored SRD content.

The census is deliberately a catalogue rather than a release switch.  It
turns every source occurrence already represented in DEKU into one immutable
record with a precise citation, a single future owning package, and a stable
key.  P-04 consumers can therefore distinguish an absent source row from a
catalogued-but-unimplemented mechanic without treating either as released.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from systems.progression import CLASS_PROGRESSION, MAX_CLASS_LEVEL
from systems.srd_class_resources import SRD_CLASS_RESOURCES
from world.chargen_data import BACKGROUNDS, SPECIES

CONTENT_CENSUS_VERSION = 1
_P04_TASKS = frozenset(
    {
        "P04-A04",
        "P04-A05",
        "P04-A06",
        "P04-A07",
        "P04-A09",
        "P04-A10",
        "P04-O01",
    }
)


class ContentCensusError(ValueError):
    """Raised when authored source content has no single accountable owner."""


@dataclass(frozen=True)
class CensusRecord:
    """One immutable SRD occurrence and its single implementation owner."""

    key: str
    kind: str
    display_name: str
    class_key: str | None
    level: int | None
    srd_reference: str
    owner_task: str
    adapter: str
    release_state: str
    adaptation: str = ""


@dataclass(frozen=True)
class ContentCensus:
    """Validated source inventory used for release and backlog diagnostics."""

    version: int
    records: tuple[CensusRecord, ...]
    fingerprint: str

    def by_key(self, key: str) -> CensusRecord:
        """Return one stable record or fail without guessing an owner."""
        try:
            return MappingProxyType({record.key: record for record in self.records})[
                key
            ]
        except KeyError as err:
            raise ContentCensusError(f"Unknown census record: {key}") from err


def build_content_census(
    records: Iterable[CensusRecord], *, version: int = CONTENT_CENSUS_VERSION
) -> ContentCensus:
    """Validate and freeze the complete authored source-record inventory."""
    if version != CONTENT_CENSUS_VERSION:
        raise ContentCensusError("Content census has an unsupported version.")
    frozen = tuple(records)
    keys = [record.key for record in frozen]
    if len(keys) != len(set(keys)):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise ContentCensusError(
            "Content census contains duplicate record keys: " + ", ".join(duplicates)
        )
    for record in frozen:
        _validate_record(record)
    _validate_feature_projection(frozen)
    payload = {"version": version, "records": [asdict(record) for record in frozen]}
    return ContentCensus(
        version,
        frozen,
        sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    )


def census_report(census: ContentCensus = None) -> Mapping[str, tuple[str, ...]]:
    """Return deterministic catalogue/release status without changing state."""
    census = SRD_CONTENT_CENSUS if census is None else census
    catalogued = tuple(
        record.key for record in census.records if record.release_state == "catalogued"
    )
    released = tuple(
        record.key for record in census.records if record.release_state == "released"
    )
    return MappingProxyType({"catalogued": catalogued, "released": released})


def _validate_record(record: CensusRecord) -> None:
    if (
        not isinstance(record.key, str)
        or not record.key
        or record.kind
        not in {
            "base_feature",
            "subclass_choice",
            "subclass_feature",
            "resource",
            "spell_access",
            "background",
            "species",
        }
        or not isinstance(record.display_name, str)
        or not record.display_name
        or record.owner_task not in _P04_TASKS
        or not isinstance(record.adapter, str)
        or not record.adapter
        or record.release_state not in {"catalogued", "released"}
        or not isinstance(record.srd_reference, str)
        or not record.srd_reference.startswith("SRD 5.2.1 ")
    ):
        raise ContentCensusError(
            f"Census record '{getattr(record, 'key', '?')}' is invalid."
        )
    if record.level is not None and (
        isinstance(record.level, bool) or not 1 <= record.level <= MAX_CLASS_LEVEL
    ):
        raise ContentCensusError(f"Census record '{record.key}' has an invalid level.")


def _validate_feature_projection(records: tuple[CensusRecord, ...]) -> None:
    """Require one census occurrence for every progression-table source row."""
    record_keys = {record.key for record in records}
    expected: set[str] = set()
    for definition in CLASS_PROGRESSION.definitions.values():
        for grants in definition.levels:
            expected.update(
                _occurrence_key(key, grants.level)
                for key in (
                    *grants.automatic_feature_keys,
                    *grants.catalogued_feature_keys,
                    *grants.catalogued_subclass_choice_keys,
                    *grants.catalogued_subclass_feature_keys,
                )
            )
            expected.update(f"spell_access:{key}" for key in grants.spell_access_keys)
            expected.update(f"resource:{key}" for key in grants.resource_keys)
    missing = expected - record_keys
    if missing:
        raise ContentCensusError(
            "Content census is missing progression occurrences: "
            + ", ".join(sorted(missing))
        )


def _task_for_adapter(adapter: str) -> str:
    """Map each reviewed future adapter to its sole frozen P-04 package."""
    if adapter == "advancement.choice":
        return "P04-A04"
    if adapter == "resources.class_feature":
        return "P04-A06"
    if adapter == "magic.class_feature":
        return "P04-A10"
    if adapter == "subclass.feature":
        return "P04-A04"
    if adapter == "character_stats.unarmored_defense":
        return "P04-A05"
    return "P04-A05"


def _default_records() -> tuple[CensusRecord, ...]:
    """Project existing source-controlled class/origin data into P04-A01."""
    records: list[CensusRecord] = []
    attached_spell_access: set[str] = set()
    for definition in CLASS_PROGRESSION.definitions.values():
        for level in range(1, MAX_CLASS_LEVEL + 1):
            grants = definition.grants_at(level)
            for feature_key in (
                *grants.automatic_feature_keys,
                *grants.catalogued_feature_keys,
            ):
                feature = CLASS_PROGRESSION.features[feature_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "base_feature",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        _task_for_adapter(feature.release_adapter),
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for choice_key in grants.catalogued_subclass_choice_keys:
                feature = CLASS_PROGRESSION.features[choice_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "subclass_choice",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        "P04-A04",
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for feature_key in grants.catalogued_subclass_feature_keys:
                feature = CLASS_PROGRESSION.features[feature_key]
                records.append(
                    CensusRecord(
                        _occurrence_key(feature.key, level),
                        "subclass_feature",
                        feature.display_name,
                        definition.key,
                        level,
                        feature.srd_reference,
                        _task_for_adapter(feature.release_adapter),
                        feature.release_adapter,
                        feature.release_state,
                    )
                )
            for resource_key in grants.resource_keys:
                resource = CLASS_PROGRESSION.resources[resource_key]
                records.append(
                    CensusRecord(
                        f"resource:{resource.key}",
                        "resource",
                        resource.display_name,
                        definition.key,
                        level,
                        resource.srd_reference,
                        "P04-A06",
                        resource.owner,
                        resource.release_state,
                    )
                )
            for access_key in grants.spell_access_keys:
                if access_key in attached_spell_access:
                    continue
                attached_spell_access.add(access_key)
                access = CLASS_PROGRESSION.spell_access[access_key]
                records.append(
                    CensusRecord(
                        f"spell_access:{access.key}",
                        "spell_access",
                        access.key,
                        definition.key,
                        level,
                        access.srd_reference,
                        "P04-A09",
                        "magic.spell_access",
                        "catalogued",
                    )
                )
    # Source records not yet attached to a released level remain visible too.
    attached_resources = {
        record.key.removeprefix("resource:")
        for record in records
        if record.kind == "resource"
    }
    for resource in SRD_CLASS_RESOURCES.values():
        if resource.key not in attached_resources:
            records.append(
                CensusRecord(
                    f"resource:{resource.key}",
                    "resource",
                    resource.display_name,
                    resource.key.split(".")[0].title(),
                    None,
                    resource.srd_reference,
                    "P04-A06",
                    "resources.class_feature",
                    "catalogued",
                )
            )
    for name in BACKGROUNDS:
        records.append(
            CensusRecord(
                f"background:{name.casefold().replace(' ', '_')}",
                "background",
                name,
                None,
                None,
                "SRD 5.2.1 p.177: Backgrounds",
                "P04-O01",
                "origin.background",
                "catalogued",
            )
        )
    for name in SPECIES:
        records.append(
            CensusRecord(
                f"species:{name.casefold().replace(' ', '_')}",
                "species",
                name,
                None,
                None,
                "SRD 5.2.1 pp.180-193: Species",
                "P04-O01",
                "origin.species",
                "catalogued",
            )
        )
    return tuple(records)


def _occurrence_key(feature_key: str, level: int) -> str:
    """Keep repeats/upgrades as distinct cited source occurrences."""
    return f"feature:{feature_key}:level:{level}"


SRD_CONTENT_CENSUS = build_content_census(_default_records())

"""Narrow ADV-06 diagnosis, plan, audit, and legacy-migration service.

This module intentionally does not offer player respecialization or arbitrary
Attribute edits.  It supplies the Phase-1 repair seam needed when durable
progression provenance predates the current schema or registry fingerprint.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from django.db import transaction
from systems.advancement import (MAX_LEVEL, AdvancementError, earned_level,
                                 expected_progression_records,
                                 migrate_progression_baseline)
from systems.progression import CLASS_PROGRESSION, RegistryValidationError

ADVANCEMENT_REPAIR_ATTRIBUTE = "advancement_repair_audit"
ADVANCEMENT_REPAIR_VERSION = 1
MAX_AUDIT_EVENTS = 128


class AdvancementRepairError(ValueError):
    """Raised when a staff-only progression repair cannot safely proceed."""


@dataclass(frozen=True)
class RepairDiagnosis:
    """A bounded, read-only view of one character's progression consistency."""

    character_id: int
    class_key: str | None
    xp: int | None
    level: int | None
    issues: tuple[str, ...]
    registry_fingerprint: str


@dataclass(frozen=True)
class RepairPlan:
    """A versioned, deterministic migration-only Phase-1 repair plan."""

    plan_id: str
    character_id: int
    target_fingerprint: str
    issues: tuple[str, ...]
    operations: tuple[str, ...]
    risk: str


def diagnose_progression(character: Any) -> RepairDiagnosis:
    """Inspect durable advancement state without normalizing or mutating it."""
    character_id = _character_id(character)
    class_key = character.attributes.get("char_class")
    xp = character.attributes.get("xp", 0)
    level = character.attributes.get("level", 1)
    issues: list[str] = []
    if not isinstance(class_key, str):
        issues.append("missing_or_unknown_class")
    else:
        try:
            CLASS_PROGRESSION.class_for(class_key)
        except RegistryValidationError:
            issues.append("missing_or_unknown_class")
    if (
        isinstance(xp, bool)
        or not isinstance(xp, int)
        or xp < 0
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= MAX_LEVEL
    ):
        issues.append("invalid_xp_or_level")
    elif earned_level(xp) != level:
        issues.append("level_xp_mismatch")
    raw = character.attributes.get("class_progression")
    if raw is None:
        issues.append("missing_progression_provenance")
    elif not _primitive_provenance(raw):
        issues.append("invalid_or_legacy_progression")
    elif raw["class_key"] != class_key:
        issues.append("progression_class_mismatch")
    elif (
        raw["registry_version"] != CLASS_PROGRESSION.version
        or raw["fingerprint"] != CLASS_PROGRESSION.fingerprint
    ):
        issues.append("progression_version_drift")
    elif isinstance(level, int) and len(raw["levels"]) != level:
        issues.append("progression_level_coverage_mismatch")
    elif isinstance(class_key, str) and isinstance(level, int):
        expected = list(expected_progression_records(class_key, level))
        actual = [record for entry in raw["levels"] for record in entry["records"]]
        if actual != expected:
            issues.append("progression_provenance_mismatch")
    quarantine = character.attributes.get("advancement_repair_required")
    if isinstance(quarantine, str) and quarantine and quarantine not in issues:
        issues.append(quarantine)
    return RepairDiagnosis(
        character_id,
        class_key if isinstance(class_key, str) else None,
        xp if isinstance(xp, int) and not isinstance(xp, bool) else None,
        level if isinstance(level, int) and not isinstance(level, bool) else None,
        tuple(sorted(set(issues))),
        CLASS_PROGRESSION.fingerprint,
    )


def plan_progression_repair(character: Any) -> RepairPlan:
    """Create an immutable, no-side-effect plan for supported baseline repair."""
    diagnosis = diagnose_progression(character)
    repairable = {
        "missing_progression_provenance",
        "invalid_or_legacy_progression",
        "progression_version_drift",
        "progression_level_coverage_mismatch",
        "progression_provenance_mismatch",
    }
    operations = (
        ("migrate_progression_baseline",)
        if diagnosis.issues and set(diagnosis.issues).issubset(repairable)
        else ()
    )
    payload = {
        "character_id": diagnosis.character_id,
        "class_key": diagnosis.class_key,
        "xp": diagnosis.xp,
        "level": diagnosis.level,
        "issues": diagnosis.issues,
        "registry_fingerprint": diagnosis.registry_fingerprint,
        "operations": operations,
    }
    return RepairPlan(
        sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        diagnosis.character_id,
        _target_fingerprint(character),
        diagnosis.issues,
        operations,
        "low" if operations else "blocked",
    )


def apply_progression_repair(
    character: Any, plan: RepairPlan, *, actor: Any | None = None, reason: str = ""
) -> RepairDiagnosis:
    """Apply one unchanged low-risk migration plan and append an audit event.

    Command/lock wiring belongs to ADV06-08; this service nevertheless records
    the actor dbref when supplied and rejects stale or non-repairable plans.
    """
    if not isinstance(plan, RepairPlan) or plan.character_id != _character_id(
        character
    ):
        raise AdvancementRepairError("Repair plan does not match this character.")
    if not isinstance(reason, str) or len(reason) > 160:
        raise AdvancementRepairError("Repair reason is invalid.")
    with transaction.atomic():
        _lock(character)
        current = plan_progression_repair(character)
        if (
            current.plan_id != plan.plan_id
            or _target_fingerprint(character) != plan.target_fingerprint
        ):
            _append_audit(character, plan, actor, reason, "stale")
            raise AdvancementRepairError("Repair plan is stale; inspect again.")
        if plan.operations != ("migrate_progression_baseline",):
            _append_audit(character, plan, actor, reason, "blocked")
            raise AdvancementRepairError(
                "This progression issue needs a higher-risk repair."
            )
        try:
            migrate_progression_baseline(character)
        except AdvancementError as err:
            _append_audit(character, plan, actor, reason, "failed")
            raise AdvancementRepairError(
                "Progression repair could not be applied."
            ) from err
        result = diagnose_progression(character)
        _append_audit(character, plan, actor, reason, "applied")
        return result


def repair_audit(character: Any) -> tuple[Mapping[str, Any], ...]:
    """Return bounded immutable audit details for authorized presentation code."""
    raw = character.attributes.get(ADVANCEMENT_REPAIR_ATTRIBUTE)
    if raw is None:
        return ()
    if not _valid_audit(raw):
        raise AdvancementRepairError("Progression repair audit needs staff repair.")
    return tuple(dict(event) for event in raw["events"])


def _primitive_provenance(raw: Any) -> bool:
    """Validate only schema shape; live comparison remains diagnosis work."""
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {"version", "class_key", "registry_version", "fingerprint", "levels"}
        or raw["version"] != 2
        or not isinstance(raw["class_key"], str)
        or not isinstance(raw["registry_version"], int)
        or not isinstance(raw["fingerprint"], str)
        or not isinstance(raw["levels"], Sequence)
        or isinstance(raw["levels"], (str, bytes))
        or not 1 <= len(raw["levels"]) <= MAX_LEVEL
    ):
        return False
    return all(
        isinstance(entry, Mapping)
        and set(entry) == {"level", "records"}
        and entry["level"] == expected_level
        and isinstance(entry["records"], Sequence)
        and not isinstance(entry["records"], (str, bytes))
        for expected_level, entry in enumerate(raw["levels"], start=1)
    )


def _target_fingerprint(character: Any) -> str:
    """Fingerprint only primitive inputs that make a dry-run plan stale."""
    payload = {
        "class_key": character.attributes.get("char_class"),
        "xp": character.attributes.get("xp"),
        "level": character.attributes.get("level"),
        "progression": character.attributes.get("class_progression"),
        "repair": character.attributes.get("advancement_repair_required"),
        "registry": CLASS_PROGRESSION.fingerprint,
    }
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _append_audit(
    character: Any, plan: RepairPlan, actor: Any | None, reason: str, outcome: str
) -> None:
    """Persist a bounded, non-secret repair audit event in the transaction."""
    raw = character.attributes.get(ADVANCEMENT_REPAIR_ATTRIBUTE)
    state = (
        {"version": ADVANCEMENT_REPAIR_VERSION, "events": []} if raw is None else raw
    )
    if not _valid_audit(state):
        raise AdvancementRepairError("Progression repair audit needs staff repair.")
    events = [dict(event) for event in state["events"]]
    events.append(
        {
            "plan_id": plan.plan_id,
            "actor_id": getattr(actor, "id", None),
            "operations": list(plan.operations),
            "reason": reason,
            "outcome": outcome,
        }
    )
    character.attributes.add(
        ADVANCEMENT_REPAIR_ATTRIBUTE,
        {"version": ADVANCEMENT_REPAIR_VERSION, "events": events[-MAX_AUDIT_EVENTS:]},
    )


def _valid_audit(raw: Any) -> bool:
    return (
        isinstance(raw, Mapping)
        and set(raw) == {"version", "events"}
        and raw["version"] == ADVANCEMENT_REPAIR_VERSION
        and isinstance(raw["events"], Sequence)
        and not isinstance(raw["events"], (str, bytes))
        and len(raw["events"]) <= MAX_AUDIT_EVENTS
    )


def _character_id(character: Any) -> int:
    value = getattr(character, "pk", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdvancementRepairError("A saved character is required.")
    return value


def _lock(character: Any) -> None:
    character.__class__.objects.select_for_update().get(pk=_character_id(character))

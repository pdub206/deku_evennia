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
from types import MappingProxyType
from typing import Any

from django.db import transaction
from django.utils import timezone
from systems.advancement import (ADVANCEMENT_ATTRIBUTE, MAX_LEVEL,
                                 AdvancementError, _valid_ledger, earned_level,
                                 expected_progression_records,
                                 migrate_progression_baseline)
from systems.magic import AccessMode
from systems.magic_actions import (MagicActionError, grant_action,
                                   has_active_concentration,
                                   inspect_magic_dependencies,
                                   inspect_magic_ownership,
                                   missing_automatic_action_grants)
from systems.magic_resources import (MAGIC_RESOURCE_ATTRIBUTE,
                                     MAGIC_RESOURCE_VERSION,
                                     _slot_resource_keys, resource_maximum)
from systems.progression import CLASS_PROGRESSION, RegistryValidationError
from systems.training import TrainingError, _choice_state

ADVANCEMENT_REPAIR_ATTRIBUTE = "advancement_repair_audit"
ADVANCEMENT_REPAIR_VERSION = 2
REPAIR_PLAN_VERSION = 7
MAX_AUDIT_EVENTS = 128
MAX_DIAGNOSIS_ISSUES = 32
_MIGRATE_BASELINE = "migrate_progression_baseline"
_CLAMP_MAGIC_RESOURCES = "clamp_magic_resource_currents"
_RECONCILE_HP_BASIS = "reconcile_hp_basis"
_REPLAY_AUTOMATIC_ACTIONS = "replay_missing_automatic_actions"
_REMOVE_DUPLICATE_PROVENANCE = "remove_duplicate_progression_records"
_AUDIT_OUTCOMES = frozenset({"applied", "blocked", "failed", "stale"})


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
class RepairOperation:
    """One closed-set repair operation with its expected bounded result."""

    key: str
    risk: str
    before_issues: tuple[str, ...]
    after_issues: tuple[str, ...]
    before_values: tuple[tuple[str, int], ...] = ()
    after_values: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class RepairPlan:
    """A versioned, deterministic, target-bound Phase-1 repair plan."""

    version: int
    plan_id: str
    character_id: int
    target_fingerprint: str
    issues: tuple[str, ...]
    operations: tuple[RepairOperation, ...]
    risk: str


def diagnose_progression(character: Any) -> RepairDiagnosis:
    """Inspect durable advancement state without normalizing or mutating it."""
    character_id = _character_id(character)
    class_key = character.attributes.get("char_class")
    xp = character.attributes.get("xp", 0)
    level = character.attributes.get("level", 1)
    issues: list[str] = []
    known_class = False
    if not isinstance(class_key, str):
        issues.append("missing_or_unknown_class")
    else:
        try:
            CLASS_PROGRESSION.class_for(class_key)
        except RegistryValidationError:
            issues.append("missing_or_unknown_class")
        else:
            known_class = True
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
    elif known_class and isinstance(level, int) and not isinstance(level, bool):
        expected = list(expected_progression_records(class_key, level))
        actual = [record for entry in raw["levels"] for record in entry["records"]]
        if actual != expected:
            if _duplicate_provenance_values(character)[0]:
                issues.append("duplicate_progression_records")
            else:
                issues.append("progression_provenance_mismatch")
    if known_class and isinstance(level, int) and not isinstance(level, bool):
        _diagnose_hit_points(character, class_key, level, issues)
        _diagnose_magic_resources(character, class_key, level, issues)
        _diagnose_magic_ownership(character, issues)
        _diagnose_magic_dependencies(character, issues)
        _diagnose_missing_automatic_actions(character, issues)
    _diagnose_ledger(character, issues)
    _diagnose_choices(
        character,
        class_key if known_class else None,
        level if isinstance(level, int) and not isinstance(level, bool) else None,
        issues,
    )
    quarantine = character.attributes.get("advancement_repair_required")
    if isinstance(quarantine, str) and quarantine and quarantine not in issues:
        issues.append(quarantine)
    return RepairDiagnosis(
        character_id,
        class_key if isinstance(class_key, str) else None,
        xp if isinstance(xp, int) and not isinstance(xp, bool) else None,
        level if isinstance(level, int) and not isinstance(level, bool) else None,
        tuple(sorted(set(issues))[:MAX_DIAGNOSIS_ISSUES]),
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
    operations: tuple[RepairOperation, ...] = ()
    if diagnosis.issues and set(diagnosis.issues).issubset(repairable):
        operations = (
            RepairOperation(
                _MIGRATE_BASELINE,
                "low",
                diagnosis.issues,
                (),
            ),
        )
    elif diagnosis.issues == ("resource_current_exceeds_maximum",):
        before_values, after_values = _resource_clamp_values(character)
        if before_values:
            operations = (
                RepairOperation(
                    _CLAMP_MAGIC_RESOURCES,
                    "low",
                    diagnosis.issues,
                    (),
                    before_values,
                    after_values,
                ),
            )
    elif diagnosis.issues == ("hp_basis_drift",):
        before_values, after_values = _hp_basis_values(character)
        if before_values:
            operations = (
                RepairOperation(
                    _RECONCILE_HP_BASIS,
                    "low",
                    diagnosis.issues,
                    (),
                    before_values,
                    after_values,
                ),
            )
    elif diagnosis.issues == ("missing_automatic_action_grants",):
        before_values, after_values = _automatic_action_grant_values(character)
        if before_values:
            operations = (
                RepairOperation(
                    _REPLAY_AUTOMATIC_ACTIONS,
                    "low",
                    diagnosis.issues,
                    (),
                    before_values,
                    after_values,
                ),
            )
    elif diagnosis.issues == ("duplicate_progression_records",):
        before_values, after_values = _duplicate_provenance_values(character)
        if before_values:
            operations = (
                RepairOperation(
                    _REMOVE_DUPLICATE_PROVENANCE,
                    "low",
                    diagnosis.issues,
                    (),
                    before_values,
                    after_values,
                ),
            )
    payload = {
        "version": REPAIR_PLAN_VERSION,
        "character_id": diagnosis.character_id,
        "class_key": diagnosis.class_key,
        "xp": diagnosis.xp,
        "level": diagnosis.level,
        "issues": diagnosis.issues,
        "registry_fingerprint": diagnosis.registry_fingerprint,
        "operations": [
            {
                "key": operation.key,
                "risk": operation.risk,
                "before_issues": operation.before_issues,
                "after_issues": operation.after_issues,
                "before_values": operation.before_values,
                "after_values": operation.after_values,
            }
            for operation in operations
        ],
    }
    return RepairPlan(
        REPAIR_PLAN_VERSION,
        sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        diagnosis.character_id,
        _target_fingerprint(character),
        diagnosis.issues,
        operations,
        "low" if operations else "blocked",
    )


def apply_progression_repair(
    character: Any,
    plan: RepairPlan,
    *,
    actor: Any | None = None,
    reason: str = "",
    source_ticket: str = "",
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
    if not isinstance(source_ticket, str) or len(source_ticket) > 160:
        raise AdvancementRepairError("Repair source ticket is invalid.")
    failure: str | None = None
    result: RepairDiagnosis | None = None
    with transaction.atomic():
        _lock(character)
        current = plan_progression_repair(character)
        if (
            current.plan_id != plan.plan_id
            or _target_fingerprint(character) != plan.target_fingerprint
        ):
            _append_audit(
                character,
                plan,
                actor,
                reason,
                source_ticket,
                "stale",
                current.issues,
            )
            failure = "Repair plan is stale; inspect again."
        elif _unsafe_to_repair(character):
            _append_audit(
                character,
                plan,
                actor,
                reason,
                source_ticket,
                "blocked",
                current.issues,
            )
            failure = (
                "Progression repair cannot run during combat or an active magic "
                "dependency."
            )
        elif tuple(operation.key for operation in plan.operations) not in {
            (_MIGRATE_BASELINE,),
            (_CLAMP_MAGIC_RESOURCES,),
            (_RECONCILE_HP_BASIS,),
            (_REPLAY_AUTOMATIC_ACTIONS,),
            (_REMOVE_DUPLICATE_PROVENANCE,),
        }:
            _append_audit(
                character,
                plan,
                actor,
                reason,
                source_ticket,
                "blocked",
                current.issues,
            )
            failure = "This progression issue needs a higher-risk repair."
        elif plan.operations[0].key == _MIGRATE_BASELINE:
            try:
                migrate_progression_baseline(character)
            except AdvancementError:
                after_issues = diagnose_progression(character).issues
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "failed",
                    after_issues,
                )
                failure = "Progression repair could not be applied."
            else:
                result = diagnose_progression(character)
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "applied",
                    result.issues,
                )
        elif plan.operations[0].key == _CLAMP_MAGIC_RESOURCES:
            try:
                _apply_resource_clamp(character, plan.operations[0])
            except AdvancementRepairError:
                after_issues = diagnose_progression(character).issues
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "failed",
                    after_issues,
                )
                failure = "Magic resource repair could not be applied."
            else:
                result = diagnose_progression(character)
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "applied",
                    result.issues,
                )
        elif plan.operations[0].key == _REPLAY_AUTOMATIC_ACTIONS:
            try:
                _replay_automatic_actions(character, plan.operations[0])
            except AdvancementRepairError:
                after_issues = diagnose_progression(character).issues
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "failed",
                    after_issues,
                )
                failure = "Automatic action repair could not be applied."
            else:
                result = diagnose_progression(character)
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "applied",
                    result.issues,
                )
        elif plan.operations[0].key == _REMOVE_DUPLICATE_PROVENANCE:
            try:
                _remove_duplicate_provenance(character, plan.operations[0])
            except AdvancementRepairError:
                after_issues = diagnose_progression(character).issues
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "failed",
                    after_issues,
                )
                failure = "Duplicate provenance repair could not be applied."
            else:
                result = diagnose_progression(character)
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "applied",
                    result.issues,
                )
        else:
            try:
                _apply_hp_basis_reconciliation(character, plan.operations[0])
            except AdvancementRepairError:
                after_issues = diagnose_progression(character).issues
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "failed",
                    after_issues,
                )
                failure = "Hit point basis repair could not be applied."
            else:
                result = diagnose_progression(character)
                _append_audit(
                    character,
                    plan,
                    actor,
                    reason,
                    source_ticket,
                    "applied",
                    result.issues,
                )
    if failure:
        raise AdvancementRepairError(failure)
    if result is None:
        raise AdvancementRepairError("Progression repair did not produce a result.")
    return result


def repair_audit(character: Any) -> tuple[Mapping[str, Any], ...]:
    """Return bounded immutable audit details for authorized presentation code."""
    raw = character.attributes.get(ADVANCEMENT_REPAIR_ATTRIBUTE)
    if raw is None:
        return ()
    if not _valid_audit(raw):
        raise AdvancementRepairError("Progression repair audit needs staff repair.")
    legacy_events = (
        raw.get("legacy_events", ()) if raw["version"] == 2 else raw["events"]
    )
    current_events = raw["events"] if raw["version"] == 2 else ()
    return tuple(
        _freeze_audit_event(event) for event in (*legacy_events, *current_events)
    )


def _diagnose_hit_points(
    character: Any, class_key: str, level: int, issues: list[str]
) -> None:
    """Compare the durable HP basis with the earned class-level history."""
    expected = _expected_hp_base(character, class_key, level)
    if expected is None:
        issues.append("invalid_hp_basis")
        return
    hp_base = character.attributes.get("hp_base")
    if isinstance(hp_base, bool) or not isinstance(hp_base, int) or hp_base < 1:
        issues.append("invalid_hp_basis")
    elif hp_base != expected:
        issues.append("hp_basis_drift")


def _expected_hp_base(character: Any, class_key: str, level: int) -> int | None:
    """Derive the earned HP basis without changing current HP or attributes."""
    try:
        definition = CLASS_PROGRESSION.class_for(class_key)
        constitution = character.stats.ability_modifier("Constitution")
    except (RegistryValidationError, ValueError):
        return None
    return definition.hit_die + (level - 1) * max(
        1, definition.fixed_hp_gain + constitution
    )


def _diagnose_ledger(character: Any, issues: list[str]) -> None:
    """Flag missing or malformed replay protection without creating a ledger."""
    ledger = character.attributes.get(ADVANCEMENT_ATTRIBUTE)
    if ledger is None:
        issues.append("missing_advancement_ledger")
    elif not _valid_ledger(ledger):
        issues.append("invalid_advancement_ledger")


def _diagnose_choices(
    character: Any, class_key: str | None, level: int | None, issues: list[str]
) -> None:
    """Compare stored choice entitlements with the earned registry records."""
    try:
        state = _choice_state(character, create=False)
    except TrainingError:
        issues.append("invalid_choice_state")
        return
    if class_key is None or level is None:
        return
    expected = _expected_choice_entitlements(class_key, level)
    if state is None:
        if expected:
            issues.append("missing_choice_entitlements")
        return
    actual = [item for bucket in ("pending", "resolved") for item in state[bucket]]
    actual_ids = [item["id"] for item in actual]
    if len(actual_ids) != len(set(actual_ids)):
        issues.append("duplicate_choice_entitlements")
    if set(actual_ids) != set(expected):
        issues.append("choice_entitlement_coverage_mismatch")
    for item in actual:
        entitlement = expected.get(item["id"])
        if entitlement is None:
            continue
        expected_level, expected_choice_key, expected_count = entitlement
        if (
            item["class_key"] != class_key
            or item["level"] != expected_level
            or item["choice_key"] != expected_choice_key
            or item["count"] != expected_count
            or item["registry_version"] != CLASS_PROGRESSION.version
        ):
            issues.append("choice_entitlement_metadata_drift")
        choice = CLASS_PROGRESSION.choices[expected_choice_key]
        selected = item["selected"]
        if (
            len(selected) > expected_count
            or len(selected) != len(set(selected))
            or any(option not in choice.legal_options for option in selected)
            or any(
                len(set(selected).intersection(exclusion)) > 1
                for exclusion in choice.mutual_exclusions
            )
        ):
            issues.append("invalid_choice_selection")
    if any(len(item["selected"]) == item["count"] for item in state["pending"]):
        issues.append("completed_choice_still_pending")
    if any(len(item["selected"]) != item["count"] for item in state["resolved"]):
        issues.append("incomplete_choice_marked_resolved")


def _expected_choice_entitlements(
    class_key: str, level: int
) -> dict[str, tuple[int, str, int]]:
    """Build primitive entitlement expectations without creating any choices."""
    definition = CLASS_PROGRESSION.class_for(class_key)
    return {
        f"{definition.key}:{earned_level}:{choice_key}": (
            earned_level,
            choice_key,
            CLASS_PROGRESSION.choices[choice_key].count,
        )
        for earned_level in range(1, level + 1)
        for choice_key in definition.grants_at(earned_level).choice_keys
    }


def _diagnose_magic_resources(
    character: Any, class_key: str, level: int, issues: list[str]
) -> None:
    """Check slot records without reading through a state-mutating accessor."""
    expected_keys = _slot_resource_keys(class_key, level)
    raw = character.attributes.get(MAGIC_RESOURCE_ATTRIBUTE)
    if raw is None:
        if expected_keys:
            issues.append("missing_magic_resource_state")
        return
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "current"}
        or raw["version"] != MAGIC_RESOURCE_VERSION
        or not isinstance(raw["current"], Mapping)
    ):
        issues.append("invalid_magic_resource_state")
        return
    current = raw["current"]
    if any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in current.items()
    ):
        issues.append("invalid_magic_resource_state")
        return
    if any(key not in current for key in expected_keys):
        issues.append("missing_magic_resource_state")
    for key in expected_keys:
        if key in current and current[key] > resource_maximum(character, key):
            issues.append("resource_current_exceeds_maximum")
            break


def _resource_clamp_values(
    character: Any,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Return exact over-maximum resource values for one low-risk repair plan."""
    class_key = character.attributes.get("char_class")
    level = character.attributes.get("level")
    raw = character.attributes.get(MAGIC_RESOURCE_ATTRIBUTE)
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not isinstance(raw, Mapping)
        or not isinstance(raw.get("current"), Mapping)
    ):
        return (), ()
    before: list[tuple[str, int]] = []
    after: list[tuple[str, int]] = []
    for key in _slot_resource_keys(class_key, level):
        current = raw["current"].get(key)
        if isinstance(current, bool) or not isinstance(current, int):
            continue
        maximum = resource_maximum(character, key)
        if current > maximum:
            before.append((key, current))
            after.append((key, maximum))
    return tuple(before), tuple(after)


def _apply_resource_clamp(character: Any, operation: RepairOperation) -> None:
    """Clamp only the exact over-maximum values named by a current plan."""
    if (
        operation.key != _CLAMP_MAGIC_RESOURCES
        or not operation.before_values
        or len(operation.before_values) != len(operation.after_values)
    ):
        raise AdvancementRepairError("Magic resource repair plan is invalid.")
    raw = character.attributes.get(MAGIC_RESOURCE_ATTRIBUTE)
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "current"}
        or raw["version"] != MAGIC_RESOURCE_VERSION
        or not isinstance(raw["current"], Mapping)
    ):
        raise AdvancementRepairError("Magic resource state needs staff repair.")
    current = dict(raw["current"])
    for (key, before), (after_key, after) in zip(
        operation.before_values, operation.after_values, strict=True
    ):
        if key != after_key or current.get(key) != before:
            raise AdvancementRepairError("Magic resource repair plan is stale.")
        if after != resource_maximum(character, key) or before <= after:
            raise AdvancementRepairError("Magic resource repair plan is invalid.")
        current[key] = after
    character.attributes.add(
        MAGIC_RESOURCE_ATTRIBUTE,
        {"version": MAGIC_RESOURCE_VERSION, "current": current},
    )


def _hp_basis_values(
    character: Any,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Calculate bounded current HP after a basis correction preserves damage."""
    class_key = character.attributes.get("char_class")
    level = character.attributes.get("level")
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
    ):
        return (), ()
    expected = _expected_hp_base(character, class_key, level)
    hp_base = character.attributes.get("hp_base")
    hp_current = character.attributes.get("hp_current")
    if (
        expected is None
        or isinstance(hp_base, bool)
        or not isinstance(hp_base, int)
        or isinstance(hp_current, bool)
        or not isinstance(hp_current, int)
        or hp_current < 0
    ):
        return (), ()
    old_maximum = character.stats.hp_max
    if hp_current > old_maximum:
        return (), ()
    missing_hp = old_maximum - hp_current
    new_maximum = old_maximum + expected - hp_base
    new_current = max(0, min(new_maximum, new_maximum - missing_hp))
    return (
        (("hp_base", hp_base), ("hp_current", hp_current)),
        (("hp_base", expected), ("hp_current", new_current)),
    )


def _apply_hp_basis_reconciliation(character: Any, operation: RepairOperation) -> None:
    """Replace one validated HP basis while preserving bounded missing HP."""
    if (
        operation.key != _RECONCILE_HP_BASIS
        or len(operation.before_values) != 2
        or len(operation.after_values) != 2
    ):
        raise AdvancementRepairError("Hit point basis repair plan is invalid.")
    before_values, after_values = _hp_basis_values(character)
    if (
        not before_values
        or operation.before_values != before_values
        or operation.after_values != after_values
    ):
        raise AdvancementRepairError("Hit point basis repair plan is stale.")
    character.db.hp_base = dict(after_values)["hp_base"]
    character.db.hp_current = dict(after_values)["hp_current"]


def _automatic_action_grant_values(
    character: Any,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Return exact absent automatic actions as a closed-set repair delta."""
    try:
        missing = missing_automatic_action_grants(character)
    except MagicActionError:
        return (), ()
    return tuple((key, 0) for key in missing), tuple((key, 1) for key in missing)


def _replay_automatic_actions(character: Any, operation: RepairOperation) -> None:
    """Restore only the currently missing released automatic action keys."""
    if (
        operation.key != _REPLAY_AUTOMATIC_ACTIONS
        or not operation.before_values
        or len(operation.before_values) != len(operation.after_values)
    ):
        raise AdvancementRepairError("Automatic action repair plan is invalid.")
    before_values, after_values = _automatic_action_grant_values(character)
    if (
        not before_values
        or operation.before_values != before_values
        or operation.after_values != after_values
    ):
        raise AdvancementRepairError("Automatic action repair plan is stale.")
    try:
        for action_key, _ in before_values:
            grant_action(character, action_key, AccessMode.INNATE)
    except MagicActionError as err:
        raise AdvancementRepairError("Automatic action repair is unavailable.") from err
    if missing_automatic_action_grants(character):
        raise AdvancementRepairError("Automatic action repair did not complete.")


def _unsafe_to_repair(character: Any) -> bool:
    """Reject immediate correction while another service owns active state."""
    from systems.combat import is_fighting

    try:
        return is_fighting(character) or has_active_concentration(character)
    except MagicActionError:
        return True


def _diagnose_magic_ownership(character: Any, issues: list[str]) -> None:
    """Delegate durable spell/action compatibility to its owning service."""
    ownership = inspect_magic_ownership(character)
    if not ownership.compatible:
        issues.append("magic_ownership_incompatible")


def _diagnose_magic_dependencies(character: Any, issues: list[str]) -> None:
    """Report orphaned concentration/effect links without cleanup side effects."""
    dependencies = inspect_magic_dependencies(character)
    if not dependencies.compatible:
        issues.append("orphaned_magic_dependencies")


def _diagnose_missing_automatic_actions(character: Any, issues: list[str]) -> None:
    """Expose absent automatic actions without restoring them during diagnosis."""
    try:
        if missing_automatic_action_grants(character):
            issues.append("missing_automatic_action_grants")
    except MagicActionError:
        # ``inspect_magic_ownership`` has already reported malformed ownership
        # state without hiding it behind a second repair label.
        return


def _duplicate_provenance_values(
    character: Any,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Return an exact duplicate-only provenance delta, if one is provable."""
    before_values, after_values, _state = _duplicate_provenance_repair_data(character)
    return before_values, after_values


def _duplicate_provenance_repair_data(
    character: Any,
) -> tuple[
    tuple[tuple[str, int], ...],
    tuple[tuple[str, int], ...],
    dict[str, Any] | None,
]:
    """Build replacement provenance only when deleting extras yields canon."""
    class_key = character.attributes.get("char_class")
    level = character.attributes.get("level")
    raw = character.attributes.get("class_progression")
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not _primitive_provenance(raw)
        or raw["class_key"] != class_key
        or raw["registry_version"] != CLASS_PROGRESSION.version
        or raw["fingerprint"] != CLASS_PROGRESSION.fingerprint
        or len(raw["levels"]) != level
    ):
        return (), (), None
    try:
        expected = expected_progression_records(class_key, level)
    except AdvancementError:
        return (), (), None
    expected_by_level = {
        earned_level: [record for record in expected if record["level"] == earned_level]
        for earned_level in range(1, level + 1)
    }
    duplicate_counts: dict[str, int] = {}
    cleaned_levels: list[dict[str, Any]] = []
    for entry in raw["levels"]:
        earned_level = entry["level"]
        expected_records = expected_by_level[earned_level]
        remaining = list(expected_records)
        cleaned_records: list[dict[str, Any]] = []
        for record in entry["records"]:
            if record in remaining:
                cleaned_records.append(dict(record))
                remaining.remove(record)
            elif record in expected_records:
                record_id = record["id"]
                duplicate_counts[record_id] = duplicate_counts.get(record_id, 1) + 1
            else:
                return (), (), None
        if remaining or cleaned_records != expected_records:
            return (), (), None
        cleaned_levels.append({"level": earned_level, "records": cleaned_records})
    if not duplicate_counts:
        return (), (), None
    before_values = tuple(sorted(duplicate_counts.items()))
    after_values = tuple((record_id, 1) for record_id, _count in before_values)
    return (
        before_values,
        after_values,
        {
            "version": raw["version"],
            "class_key": raw["class_key"],
            "registry_version": raw["registry_version"],
            "fingerprint": raw["fingerprint"],
            "levels": cleaned_levels,
        },
    )


def _remove_duplicate_provenance(character: Any, operation: RepairOperation) -> None:
    """Remove only exact duplicate provenance records from a current plan."""
    if (
        operation.key != _REMOVE_DUPLICATE_PROVENANCE
        or not operation.before_values
        or len(operation.before_values) != len(operation.after_values)
    ):
        raise AdvancementRepairError("Duplicate provenance repair plan is invalid.")
    before_values, after_values, state = _duplicate_provenance_repair_data(character)
    if (
        state is None
        or operation.before_values != before_values
        or operation.after_values != after_values
    ):
        raise AdvancementRepairError("Duplicate provenance repair plan is stale.")
    character.attributes.add("class_progression", state)


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
    """Fingerprint every diagnosis input that can make a plan unsafe to apply."""
    payload = {
        "class_key": character.attributes.get("char_class"),
        "xp": character.attributes.get("xp"),
        "level": character.attributes.get("level"),
        "hp_base": character.attributes.get("hp_base"),
        "ledger": character.attributes.get(ADVANCEMENT_ATTRIBUTE),
        "progression": character.attributes.get("class_progression"),
        "choices": character.attributes.get("progression_choices"),
        "magic_resources": character.attributes.get(MAGIC_RESOURCE_ATTRIBUTE),
        "magic_actions": character.attributes.get("magic_action_state"),
        "magic_concentration": character.attributes.get("magic_concentration"),
        "active_effects": character.attributes.get("active_effects"),
        "repair": character.attributes.get("advancement_repair_required"),
        "registry": CLASS_PROGRESSION.fingerprint,
    }
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _append_audit(
    character: Any,
    plan: RepairPlan,
    actor: Any | None,
    reason: str,
    source_ticket: str,
    outcome: str,
    after_issues: tuple[str, ...],
) -> None:
    """Persist a bounded, non-secret repair audit event in the transaction."""
    raw = character.attributes.get(ADVANCEMENT_REPAIR_ATTRIBUTE)
    if raw is not None and not _valid_audit(raw):
        raise AdvancementRepairError("Progression repair audit needs staff repair.")
    legacy_events, events = _audit_events_for_append(raw)
    if len(legacy_events) >= MAX_AUDIT_EVENTS:
        raise AdvancementRepairError("Progression repair audit capacity is exhausted.")
    events.append(
        {
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "target_id": plan.character_id,
            "target_fingerprint": plan.target_fingerprint,
            "actor_id": _actor_id(actor),
            "operations": [operation.key for operation in plan.operations],
            "reason": reason,
            "source_ticket": source_ticket,
            "outcome": outcome,
            "before_issues": list(plan.issues),
            "after_issues": list(after_issues),
            "recorded_at": timezone.now().isoformat(),
        }
    )
    character.attributes.add(
        ADVANCEMENT_REPAIR_ATTRIBUTE,
        {
            "version": ADVANCEMENT_REPAIR_VERSION,
            "legacy_events": legacy_events,
            "events": events[-(MAX_AUDIT_EVENTS - len(legacy_events)) :],
        },
    )


def _audit_events_for_append(
    raw: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Preserve valid version-one events while writing the richer version-two form."""
    if raw is None:
        return [], []
    if raw["version"] == 1:
        return [dict(event) for event in raw["events"]], []
    return (
        [dict(event) for event in raw.get("legacy_events", ())],
        [dict(event) for event in raw["events"]],
    )


def _valid_audit(raw: Any) -> bool:
    if not isinstance(raw, Mapping):
        return False
    if raw.get("version") == 1:
        return set(raw) == {"version", "events"} and _event_sequence(
            raw["events"], _valid_legacy_audit_event
        )
    if raw.get("version") != ADVANCEMENT_REPAIR_VERSION:
        return False
    if set(raw) not in ({"version", "events"}, {"version", "legacy_events", "events"}):
        return False
    legacy_events = raw.get("legacy_events", ())
    return (
        _event_sequence(raw["events"], _valid_audit_event)
        and _event_sequence(legacy_events, _valid_legacy_audit_event)
        and len(raw["events"]) + len(legacy_events) <= MAX_AUDIT_EVENTS
    )


def _event_sequence(value: Any, validator: Any) -> bool:
    """Validate one bounded audit-event sequence without treating text as rows."""
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) <= MAX_AUDIT_EVENTS
        and all(validator(event) for event in value)
    )


def _valid_legacy_audit_event(event: Any) -> bool:
    """Read the pre-enriched audit shape without pretending it has new fields."""
    return (
        isinstance(event, Mapping)
        and set(event) == {"plan_id", "actor_id", "operations", "reason", "outcome"}
        and _digest(event["plan_id"])
        and (
            event["actor_id"] is None
            or (
                isinstance(event["actor_id"], int)
                and not isinstance(event["actor_id"], bool)
                and event["actor_id"] > 0
            )
        )
        and _string_sequence(
            event["operations"],
            allowed={
                _MIGRATE_BASELINE,
                _CLAMP_MAGIC_RESOURCES,
                _RECONCILE_HP_BASIS,
            },
        )
        and isinstance(event["reason"], str)
        and len(event["reason"]) <= 160
        and event["outcome"] in _AUDIT_OUTCOMES
    )


def _valid_audit_event(event: Any) -> bool:
    """Validate a bounded primitive audit event before staff code reads it."""
    required = {
        "plan_id",
        "plan_version",
        "target_id",
        "target_fingerprint",
        "actor_id",
        "operations",
        "reason",
        "source_ticket",
        "outcome",
        "before_issues",
        "after_issues",
        "recorded_at",
    }
    return (
        isinstance(event, Mapping)
        and set(event) == required
        and _digest(event["plan_id"])
        and isinstance(event["plan_version"], int)
        and not isinstance(event["plan_version"], bool)
        and 1 <= event["plan_version"] <= REPAIR_PLAN_VERSION
        and isinstance(event["target_id"], int)
        and not isinstance(event["target_id"], bool)
        and event["target_id"] > 0
        and _digest(event["target_fingerprint"])
        and (
            event["actor_id"] is None
            or (
                isinstance(event["actor_id"], int)
                and not isinstance(event["actor_id"], bool)
                and event["actor_id"] > 0
            )
        )
        and _string_sequence(
            event["operations"],
            allowed={
                _MIGRATE_BASELINE,
                _CLAMP_MAGIC_RESOURCES,
                _RECONCILE_HP_BASIS,
            },
        )
        and isinstance(event["reason"], str)
        and len(event["reason"]) <= 160
        and isinstance(event["source_ticket"], str)
        and len(event["source_ticket"]) <= 160
        and event["outcome"] in _AUDIT_OUTCOMES
        and _string_sequence(event["before_issues"])
        and _string_sequence(event["after_issues"])
        and isinstance(event["recorded_at"], str)
        and 1 <= len(event["recorded_at"]) <= 64
    )


def _string_sequence(value: Any, *, allowed: set[str] | None = None) -> bool:
    """Accept a bounded primitive string sequence, optionally from one schema."""
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) <= MAX_DIAGNOSIS_ISSUES
        and all(
            isinstance(item, str)
            and 0 < len(item) <= 96
            and (allowed is None or item in allowed)
            for item in value
        )
    )


def _digest(value: Any) -> bool:
    """Return whether a persisted identifier has the required SHA-256 shape."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _freeze_audit_event(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose only detached immutable audit primitives to presentation code."""
    return MappingProxyType(
        {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in event.items()
        }
    )


def _actor_id(actor: Any | None) -> int | None:
    """Record a stable staff identity without accepting unsaved live objects."""
    if actor is None:
        return None
    value = getattr(actor, "pk", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdvancementRepairError("Repair actor must be saved.")
    return value


def _character_id(character: Any) -> int:
    value = getattr(character, "pk", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdvancementRepairError("A saved character is required.")
    return value


def _lock(character: Any) -> None:
    character.__class__.objects.select_for_update().get(pk=_character_id(character))

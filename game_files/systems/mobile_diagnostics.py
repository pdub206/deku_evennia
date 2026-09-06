"""MOB-08's side-effect-free mobile inspection and failure retention service.

This module owns presentation-ready, primitive snapshots.  It deliberately
does not repair mobile data: each MOB subsystem remains the authority for its
own state and exposes only validated diagnostic readers here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from evennia.prototypes.prototypes import search_prototype
from evennia.utils import logger

MOBILE_FAILURE_ATTRIBUTE = "mobile_last_failure"
MOBILE_FAILURE_VERSION = 1
MOBILE_DIAGNOSTIC_VERSION = 1
MAX_FAILURE_RELATED_IDS = 6


class MobileDiagnosticError(ValueError):
    """A requested mobile diagnostic operation is unsafe or unsupported."""


def mobile_diagnostic_snapshot(npc: Any) -> dict[str, Any]:
    """Return a bounded, side-effect-free snapshot for one live NPC.

    Broken readers become an ``unavailable`` section so one historic malformed
    Attribute never prevents a Builder from seeing the independently readable
    parts of an NPC.
    """
    if getattr(getattr(npc, "db", None), "is_player_character", None) is not False:
        raise MobileDiagnosticError("Only live NPCs have mobile diagnostics.")
    if not _positive_id(getattr(npc, "id", None)):
        raise MobileDiagnosticError("The NPC has no durable identity.")

    from systems.areas import area_of, room_key_of
    from systems.combat import get_encounter_id, get_target, is_fighting
    from systems.mob_combat import mobile_combat_snapshot
    from systems.mob_spawning import mobile_spawn_identity
    from systems.mobile_navigation import navigation_state
    from systems.mobile_policy import mobile_policy
    from systems.mobile_relationships import relationship_state
    from systems.mobile_specials import mobile_specials
    from systems.mobiles import mobile_behavior_snapshot

    location = getattr(npc, "location", None)
    identity = {
        "dbref": _dbref(npc),
        "key": _safe_key(getattr(npc, "key", "")),
        "location_dbref": _dbref(location),
        "area_key": _safe_location_key(area_of, location),
        "room_key": _safe_location_key(room_key_of, location),
    }
    sections = {
        "runner": _reader(mobile_behavior_snapshot, npc),
        "policy": _reader(mobile_policy, npc),
        "combat": _reader(mobile_combat_snapshot, npc),
        "navigation": _reader(navigation_state, npc),
        "specials": _reader(mobile_specials, npc),
        "relationships": _reader(relationship_state, npc),
        "failure": _reader(mobile_failure_record, npc),
    }
    try:
        spawn = mobile_spawn_identity(npc)
        sections["spawn"] = {
            "status": "available",
            "data": (
                {"identity": "untemplated"}
                if spawn is None
                else {
                    "identity": "managed" if spawn.managed else "unmanaged",
                    "prototype_key": spawn.prototype_key,
                    "reset_owner": spawn.reset_owner,
                    "area_key": spawn.area_key,
                    "placement_key": spawn.placement_key,
                    "reset_room_key": spawn.reset_room_key,
                }
            ),
        }
    except Exception:
        sections["spawn"] = _unavailable()

    combat = sections["combat"].get("data", {})
    if sections["combat"]["status"] == "available":
        try:
            combat["fighting"] = is_fighting(npc)
            combat["encounter_id"] = get_encounter_id(npc)
            combat["target_dbref"] = _dbref(get_target(npc))
        except Exception:
            combat["fighting"] = None
            combat["encounter_id"] = None
            combat["target_dbref"] = None
    return {
        "version": MOBILE_DIAGNOSTIC_VERSION,
        "kind": "live",
        "identity": identity,
        "sections": sections,
        "findings": _findings(npc, sections),
    }


def mobile_template_snapshot(prototype_key: str) -> dict[str, Any]:
    """Inspect validated authored defaults without confusing them with runtime."""
    if not isinstance(prototype_key, str) or not prototype_key:
        raise MobileDiagnosticError("A prototype key is required.")
    matches = [
        proto
        for proto in search_prototype(prototype_key)
        if proto.get("prototype_key") == prototype_key
        and proto.get("typeclass") == "typeclasses.characters.Character"
    ]
    if len(matches) != 1:
        raise MobileDiagnosticError("NPC prototype is missing or ambiguous.")
    source = _flatten_prototype(matches[0])
    from systems.mob_combat import validate_combat_profile
    from systems.mobile_policy import (MOBILE_POLICY_ATTRIBUTE,
                                       default_mobile_policy,
                                       validate_mobile_policy)
    from systems.mobile_specials import (MOBILE_SPECIALS_ATTRIBUTE,
                                         default_mobile_specials,
                                         validate_mobile_specials)
    from systems.mobiles import (MOBILE_BEHAVIOR_PROFILE_ATTRIBUTE,
                                 initial_mobile_state)

    return {
        "version": MOBILE_DIAGNOSTIC_VERSION,
        "kind": "template",
        "identity": {
            "prototype_key": prototype_key,
            "key": _safe_key(source.get("key", "")),
        },
        "authored": {
            "runner": _reader(
                initial_mobile_state,
                source.get(MOBILE_BEHAVIOR_PROFILE_ATTRIBUTE, "idle"),
            ),
            "policy": _reader(
                validate_mobile_policy,
                source.get(MOBILE_POLICY_ATTRIBUTE, default_mobile_policy()),
            ),
            "combat": _reader(
                validate_combat_profile,
                source.get(
                    "mob_combat_profile",
                    {
                        "version": 1,
                        "target_policy": "current",
                        "tactics": [],
                        "wimpy": 0,
                    },
                ),
            ),
            "specials": _reader(
                validate_mobile_specials,
                source.get(MOBILE_SPECIALS_ATTRIBUTE, default_mobile_specials()),
            ),
        },
        "runtime": "unavailable_for_template",
    }


def mobile_population_snapshot(area_key: str) -> dict[str, Any]:
    """Return MOB-05's fresh counts in a deterministic diagnostic envelope."""
    from systems.mob_spawning import \
        mobile_population_snapshot as count_population

    snapshot = count_population(area_key)
    return {
        "version": MOBILE_DIAGNOSTIC_VERSION,
        "area_key": snapshot.area_key,
        "placement_counts": dict(sorted(snapshot.placement_counts.items())),
        "room_prototype_counts": {
            room: dict(sorted(counts.items()))
            for room, counts in sorted(snapshot.room_prototype_counts.items())
        },
        "area_prototype_counts": dict(sorted(snapshot.area_prototype_counts.items())),
    }


def mobile_compact_summary(npc: Any) -> str:
    """Render the safe one-line MOB-08 addition for Builder ``show`` output."""
    snapshot = mobile_diagnostic_snapshot(npc)
    spawn = snapshot["sections"]["spawn"]
    runner = snapshot["sections"]["runner"]
    spawn_data = spawn.get("data", {})
    runner_data = runner.get("data", {})
    identity = spawn_data.get("identity", "unavailable")
    prototype = spawn_data.get("prototype_key", "none")
    behavior = runner_data.get("behavior_key", "unavailable")
    return f"identity={identity}; prototype={prototype}; behavior={behavior}"


def mobile_failure_record(npc: Any) -> dict[str, Any] | None:
    """Read the retained last failure without treating absence as an error."""
    raw = npc.attributes.get(MOBILE_FAILURE_ATTRIBUTE)
    return None if raw is None else _validate_failure(raw)


def record_mobile_failure(
    npc: Any,
    subsystem: str,
    reason: str,
    *,
    token: int | None = None,
    behavior_key: str | None = None,
    action_key: str | None = None,
    purpose: str | None = None,
    related_ids: tuple[str, ...] = (),
) -> None:
    """Retain one safe mobile failure, never allowing bookkeeping to raise.

    Identical successive failures are coalesced.  The record intentionally
    accepts only stable internal reason keys and safe dbrefs/placement keys;
    callers must never pass exception text, locks, or player command text.
    """
    try:
        _require_npc(npc)
        candidate = _failure_payload(
            subsystem, reason, token, behavior_key, action_key, purpose, related_ids
        )
        previous = mobile_failure_record(npc)
        if previous and _failure_signature(previous) == _failure_signature(candidate):
            candidate["sequence"] = previous["sequence"]
            candidate["occurred_at"] = previous["occurred_at"]
            candidate["repeat_count"] = previous["repeat_count"] + 1
        elif previous:
            candidate["sequence"] = previous["sequence"] + 1
        npc.attributes.add(MOBILE_FAILURE_ATTRIBUTE, candidate)
    except Exception:
        # Diagnostics are strictly observational; a bad diagnostic store may
        # not turn a valid movement, combat action, or pulse into a failure.
        return


def mark_mobile_failure_recovered(npc: Any) -> None:
    """Mark, but do not erase, the retained failure after a later success."""
    try:
        previous = mobile_failure_record(npc)
        if previous is None or previous["recovered_at"] is not None:
            return
        previous["recovered_at"] = _timestamp()
        npc.attributes.add(MOBILE_FAILURE_ATTRIBUTE, _validate_failure(previous))
    except Exception:
        return


def clear_mobile_failure(npc: Any, *, audited_by: Any) -> bool:
    """Explicitly clear retained failure data after Builder authorization."""
    _require_npc(npc)
    try:
        allowed = audited_by.permissions.check("Builder")
    except Exception:
        allowed = False
    if not allowed:
        raise MobileDiagnosticError("Builder permission is required to clear failures.")
    if mobile_failure_record(npc) is None:
        return False
    npc.attributes.remove(MOBILE_FAILURE_ATTRIBUTE)
    logger.log_info(
        "MOB-08 failure cleared: " f"npc={_dbref(npc)} builder={_dbref(audited_by)}."
    )
    return True


def _reader(callback: Any, *args: Any) -> dict[str, Any]:
    try:
        return {"status": "available", "data": _primitive(callback(*args))}
    except Exception:
        return _unavailable()


def _unavailable() -> dict[str, Any]:
    return {"status": "unavailable", "reason": "reader_unavailable"}


def _findings(npc: Any, sections: Mapping[str, Any]) -> list[dict[str, str]]:
    """Report independent invariant concerns without repairing any one of them."""
    findings: list[dict[str, str]] = []

    def add(severity: str, subsystem: str, reason: str, owner: str) -> None:
        findings.append(
            {
                "severity": severity,
                "subsystem": subsystem,
                "reason": reason,
                "owner": owner,
            }
        )

    for name, section in sections.items():
        if section.get("status") == "unavailable":
            add("warning", name, "subsystem_unavailable", f"MOB-{_owner(name)}")
    runner = sections.get("runner", {}).get("data")
    if isinstance(runner, Mapping):
        if runner.get("next_eligible_token", 1) <= runner.get("last_consumed_token", 0):
            add("error", "runner", "regressed_token", "MOB-01")
        from systems.mobiles import behavior_profile_keys

        if runner.get("behavior_key") not in behavior_profile_keys():
            add("error", "runner", "unknown_behavior", "MOB-01")
    nav = sections.get("navigation", {}).get("data", {})
    relationship = sections.get("relationships", {}).get("data", {})
    combat = sections.get("combat", {}).get("data", {})
    nav = nav if isinstance(nav, Mapping) else {}
    relationship = relationship if isinstance(relationship, Mapping) else {}
    combat = combat if isinstance(combat, Mapping) else {}
    if combat.get("fighting") and (nav.get("pursuit") or relationship.get("follow")):
        add("warning", "navigation", "fighting_competing_work", "MOB-04")
    if getattr(npc, "location", None) is None and isinstance(runner, Mapping):
        add("warning", "runner", "scheduled_off_grid", "MOB-01")
    return sorted(
        findings, key=lambda item: (item["severity"], item["subsystem"], item["reason"])
    )


def _owner(section: str) -> str:
    return {
        "runner": "01",
        "policy": "03",
        "combat": "02",
        "navigation": "04",
        "spawn": "05",
        "specials": "06",
        "relationships": "07",
        "failure": "08",
    }.get(section, "08")


def _failure_payload(
    subsystem: Any,
    reason: Any,
    token: Any,
    behavior_key: Any,
    action_key: Any,
    purpose: Any,
    related_ids: Any,
) -> dict[str, Any]:
    for value, label in ((subsystem, "subsystem"), (reason, "reason")):
        if not _safe_code(value):
            raise MobileDiagnosticError(f"Mobile failure {label} is invalid.")
    if token is not None and (
        isinstance(token, bool) or not isinstance(token, int) or token < 0
    ):
        raise MobileDiagnosticError("Mobile failure token is invalid.")
    values = (behavior_key, action_key, purpose)
    if any(value is not None and not _safe_code(value) for value in values):
        raise MobileDiagnosticError("Mobile failure key is invalid.")
    if (
        not isinstance(related_ids, tuple)
        or len(related_ids) > MAX_FAILURE_RELATED_IDS
        or any(not _safe_related(value) for value in related_ids)
    ):
        raise MobileDiagnosticError("Mobile failure related identifiers are invalid.")
    return {
        "version": MOBILE_FAILURE_VERSION,
        "subsystem": subsystem,
        "reason": reason,
        "token": token,
        "behavior_key": behavior_key,
        "action_key": action_key,
        "purpose": purpose,
        "related_ids": list(related_ids),
        "sequence": 1,
        "occurred_at": _timestamp(),
        "repeat_count": 1,
        "recovered_at": None,
    }


def _validate_failure(raw: Any) -> dict[str, Any]:
    expected = {
        "version",
        "subsystem",
        "reason",
        "token",
        "behavior_key",
        "action_key",
        "purpose",
        "related_ids",
        "sequence",
        "occurred_at",
        "repeat_count",
        "recovered_at",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != expected
        or raw.get("version") != MOBILE_FAILURE_VERSION
    ):
        raise MobileDiagnosticError("Mobile failure record is invalid.")
    result = _failure_payload(
        raw["subsystem"],
        raw["reason"],
        raw["token"],
        raw["behavior_key"],
        raw["action_key"],
        raw["purpose"],
        tuple(raw["related_ids"]),
    )
    if (
        not all(
            isinstance(raw[name], int)
            and not isinstance(raw[name], bool)
            and raw[name] >= 1
            for name in ("sequence", "repeat_count")
        )
        or not _safe_timestamp(raw["occurred_at"])
        or (
            raw["recovered_at"] is not None and not _safe_timestamp(raw["recovered_at"])
        )
    ):
        raise MobileDiagnosticError("Mobile failure record has invalid values.")
    result.update(
        sequence=raw["sequence"],
        occurred_at=raw["occurred_at"],
        repeat_count=raw["repeat_count"],
        recovered_at=raw["recovered_at"],
    )
    return result


def _failure_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        record[name] if name != "related_ids" else tuple(record[name])
        for name in (
            "subsystem",
            "reason",
            "token",
            "behavior_key",
            "action_key",
            "purpose",
            "related_ids",
        )
    )


def _flatten_prototype(proto: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: deepcopy(value) for key, value in proto.items() if key != "attrs"}
    for entry in proto.get("attrs", []):
        if (
            isinstance(entry, (list, tuple))
            and len(entry) >= 2
            and isinstance(entry[0], str)
        ):
            result[entry[0]] = deepcopy(entry[1])
    return result


def _primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return {key: _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    raise MobileDiagnosticError("Diagnostic reader returned non-primitive data.")


def _require_npc(npc: Any) -> None:
    if getattr(getattr(npc, "db", None), "is_player_character", None) is not False:
        raise MobileDiagnosticError("Only NPCs have mobile failure records.")


def _dbref(value: Any) -> str | None:
    return f"#{value.id}" if _positive_id(getattr(value, "id", None)) else None


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_key(value: Any) -> str:
    return value if isinstance(value, str) and len(value) <= 80 else ""


def _safe_key_or_none(value: Any) -> str | None:
    return _safe_key(value) or None


def _safe_location_key(reader: Any, location: Any) -> str | None:
    """Read a location key without making an off-grid NPC uninspectable."""
    try:
        return _safe_key_or_none(reader(location))
    except Exception:
        return None


def _safe_code(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 80
        and all(char.islower() or char.isdigit() or char in "._-" for char in value)
    )


def _safe_related(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 80
        and (value.startswith("#") or _safe_code(value))
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _safe_timestamp(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 64

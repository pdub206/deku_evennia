"""ADV-01's canonical, transactional experience and level service.

The module deliberately owns only XP, level, and the fixed hit-point gain
available before ADV-02's progression registry exists.  Callers must use a
stable source identity; a duplicate identity is a durable no-op.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from django.db import transaction
from systems.magic_release_manifest import is_level_published
from systems.progression import CLASS_PROGRESSION, RegistryValidationError

MAX_LEVEL = 20
MAX_LEDGER_ENTRIES = 128
MAX_COMPACTED_SOURCES = 4096
ADVANCEMENT_ATTRIBUTE = "advancement_ledger"
ADVANCEMENT_VERSION = 1
PROGRESSION_ATTRIBUTE = "class_progression"
PROGRESSION_VERSION = 2
_LEVEL_APPLICATION_ATTRIBUTES = (
    "hp_base",
    "level",
    PROGRESSION_ATTRIBUTE,
    "progression_choices",
    "magic_action_state",
    "magic_resources",
)
_LEVEL_ONE_INITIALIZATION_ATTRIBUTES = (
    "char_class",
    "xp",
    "level",
    *_LEVEL_APPLICATION_ATTRIBUTES,
)

# SRD 5.2.1, cumulative experience points for character levels 1--20.
XP_THRESHOLDS = (
    0,
    300,
    900,
    2700,
    6500,
    14000,
    23000,
    34000,
    48000,
    64000,
    85000,
    100000,
    120000,
    140000,
    165000,
    195000,
    225000,
    265000,
    305000,
    355000,
)


class AdvancementError(ValueError):
    """Raised when an XP operation cannot safely be completed."""


@dataclass(frozen=True)
class AdvancementResult:
    """The committed or idempotently recovered outcome of an XP operation."""

    old_xp: int
    new_xp: int
    old_level: int
    new_level: int
    crossed_thresholds: tuple[int, ...]
    applied_grants: tuple[str, ...]
    pending_choices: tuple[str, ...]
    capped: bool
    applied: bool
    reason: str


def earned_level(xp: int) -> int:
    """Return the effective 1--20 level earned by a cumulative XP total."""
    _non_negative_integer(xp, "XP")
    return min(MAX_LEVEL, sum(xp >= threshold for threshold in XP_THRESHOLDS))


def award_xp(
    character: Any, amount: int, *, source_kind: str, source_id: str | int
) -> AdvancementResult:
    """Atomically award non-negative XP once for a durable source identity.

    A database row lock serializes awards for a character.  No player messages
    are sent here: callers may notify only after this function returns a
    committed result.
    """
    _non_negative_integer(amount, "XP award")
    source = _source_digest(source_kind, source_id)
    _validate_character(character)

    with transaction.atomic():
        _lock_character(character)
        ledger = _ledger(character)
        prior = _find_entry(ledger, source)
        if prior is not None:
            if prior["amount"] != amount:
                return _result_from_payload(
                    prior["result"], applied=False, reason="conflicting_source"
                )
            return _result_from_payload(
                prior["result"], applied=False, reason="duplicate_source"
            )
        if source in ledger["compacted_sources"]:
            return AdvancementResult(
                character.stats.xp,
                character.stats.xp,
                character.stats.level,
                character.stats.level,
                (),
                (),
                (),
                character.stats.level == MAX_LEVEL,
                False,
                "compacted_source",
            )

        old_xp, stored_level = _stored_xp_and_level(character)
        old_level = earned_level(old_xp)
        if stored_level != old_level:
            _mark_repair_required(character, "level_xp_mismatch")
            raise AdvancementError("Character advancement requires staff repair.")

        new_xp = old_xp + amount
        new_level = earned_level(new_xp)
        if new_level > old_level and not is_level_published(
            character.attributes.get("char_class"), new_level
        ):
            raise AdvancementError("Class progression is not released for that level.")
        missing_hp = character.stats.hp_max - character.stats.hp_current
        crossed = tuple(
            threshold
            for threshold in XP_THRESHOLDS[old_level:new_level]
            if threshold <= new_xp
        )
        level_snapshot = _level_application_snapshot(character)
        try:
            grants, pending_choices = _apply_levels(character, old_level, new_level)
        except Exception:
            _restore_level_application_snapshot(character, level_snapshot)
            raise
        character.db.xp = new_xp
        character.db.level = new_level
        _preserve_missing_hp(character, missing_hp)
        result = AdvancementResult(
            old_xp,
            new_xp,
            old_level,
            new_level,
            crossed,
            tuple(grants),
            pending_choices,
            new_level == MAX_LEVEL,
            True,
            "awarded" if amount else "zero_award",
        )
        ledger["entries"].append(
            {"source": source, "amount": amount, "result": _result_payload(result)}
        )
        _compact_ledger(ledger)
        _write_ledger(character, ledger)
    return result


def initialize_level_one(character: Any, *, class_key: str, hp_base: int) -> None:
    """Write the chargen level-one baseline without replaying side effects.

    This is intentionally not an XP award.  Chargen is the authoritative
    creator of its equipment and menus; ADV-01 only records its zero-XP
    advancement baseline.
    """
    _non_negative_integer(hp_base, "HP base")
    if not isinstance(class_key, str) or not class_key:
        raise AdvancementError("A canonical class is required.")
    try:
        definition = CLASS_PROGRESSION.class_for(class_key)
    except RegistryValidationError as err:
        raise AdvancementError("A canonical class is required.") from err
    if hp_base != definition.hit_die:
        raise AdvancementError("Level-one HP must match the class progression.")
    with transaction.atomic():
        _lock_character(character)
        snapshot = _attribute_snapshot(character, _LEVEL_ONE_INITIALIZATION_ATTRIBUTES)
        try:
            character.db.char_class = class_key
            character.db.xp = 0
            character.db.level = 1
            character.db.hp_base = max(1, hp_base)
            _write_progression_state(
                character,
                _new_progression_state(definition.key, through_level=1),
            )
            _apply_level_grants(character, definition, 1)
            _write_ledger(character, _new_ledger())
        except Exception:
            _restore_attribute_snapshot(
                character, snapshot, _LEVEL_ONE_INITIALIZATION_ATTRIBUTES
            )
            raise


def _apply_levels(
    character: Any, old_level: int, new_level: int
) -> tuple[list[str], tuple[str, ...]]:
    """Apply HP and create registry-defined choice entitlements per level."""
    if new_level <= old_level:
        return [], ()
    try:
        definition = CLASS_PROGRESSION.class_for(character.attributes.get("char_class"))
    except RegistryValidationError as err:
        raise AdvancementError("Character advancement requires staff repair.") from err
    constitution = character.stats.ability_modifier("Constitution")
    gain = max(1, definition.fixed_hp_gain + constitution)
    hp_base = character.stats.hp_base + gain * (new_level - old_level)
    pending: list[str] = []
    grants: list[str] = []
    progression = _progression_state(character)
    if progression["class_key"] != definition.key:
        _mark_repair_required(character, "progression_class_mismatch")
        raise AdvancementError("Character advancement requires staff repair.")
    for level in range(old_level + 1, new_level + 1):
        level_grants = definition.grants_at(level)
        choices = level_grants.choice_keys
        _append_level_provenance(progression, definition.key, level)
        # Resource and action adapters must see the coordinate being earned,
        # not the old persisted level, so a newly introduced slot receives its
        # explicit initial capacity.  The caller's rollback snapshot restores
        # this temporary write if any adapter rejects the grant.
        character.db.level = level
        _apply_level_grants(character, definition, level)
        grants.extend(level_grants.automatic_feature_keys)
        pending.extend(choices)
    # Write HP only after every adapter for every crossed level has succeeded.
    character.db.hp_base = hp_base
    _write_progression_state(character, progression)
    return (
        [
            *(f"hp_level_{level}" for level in range(old_level + 1, new_level + 1)),
            *grants,
        ],
        tuple(pending),
    )


def _apply_level_grants(character: Any, definition: Any, level: int) -> None:
    """Apply one already-recorded level's released grants exactly once.

    The durable progression record is appended before this function is called
    by the level-up transaction.  The surrounding database transaction makes
    a failed action/resource/choice adapter roll back both pieces together.
    """
    grants = definition.grants_at(level)
    from systems.training import initialize_choice_entitlements

    initialize_choice_entitlements(character, definition.key, level)
    _grant_automatic_actions(character, grants)
    _initialize_granted_resources(character, grants)
    _initialize_spell_access(character, grants)


def _grant_automatic_actions(character: Any, grants: Any) -> None:
    """Grant released feature actions through MAGIC-02's ownership boundary."""
    try:
        from systems.magic import AccessMode
        from systems.magic_actions import MagicActionError, grant_action

        for feature_key in grants.automatic_feature_keys:
            feature = CLASS_PROGRESSION.features[feature_key]
            if feature.action_key:
                grant_action(character, feature.action_key, AccessMode.INNATE)
    except (KeyError, MagicActionError) as err:
        raise AdvancementError("Character advancement requires staff repair.") from err


def _initialize_granted_resources(character: Any, grants: Any) -> None:
    """Create an explicit initial current value for newly released resources.

    A later maximum increase deliberately preserves the existing current
    value.  This closes the old implicit-default behaviour where a missing
    resource entry could look like an accidental refill after an upgrade.
    """
    if not grants.resource_keys:
        return
    try:
        from systems.magic_resources import (MagicResourceError,
                                             initialize_resource)

        for resource_key in grants.resource_keys:
            initialize_resource(character, resource_key)
    except MagicResourceError as err:
        raise AdvancementError("Character advancement requires staff repair.") from err


def _initialize_spell_access(character: Any, grants: Any) -> None:
    """Create the current-value records for spell slots earned at this level.

    This is deliberately separate from non-spell resources: a spell-access
    table is present at every class level, so the owning resource service can
    distinguish a capacity increase from a newly introduced slot key.
    """
    if not grants.spell_access_keys:
        return
    try:
        from systems.magic_resources import (MagicResourceError,
                                             initialize_spell_access_resources)

        for spell_access_key in grants.spell_access_keys:
            initialize_spell_access_resources(character, spell_access_key)
    except MagicResourceError as err:
        raise AdvancementError("Character advancement requires staff repair.") from err


def _preserve_missing_hp(character: Any, missing_hp: int) -> None:
    """Keep damage taken constant when a level increases maximum HP."""
    # hp_base and level have already changed.  Deliberately write the resource
    # directly: leveling must not emit combat-policy effects before commit.
    character.db.hp_current = max(0, character.stats.hp_max - missing_hp)


def _level_application_snapshot(character: Any) -> dict[str, Any]:
    """Capture only state the level-grant adapters are allowed to mutate.

    Evennia Attributes use their own handler layer.  The surrounding database
    transaction is still authoritative, but retaining this narrow primitive
    snapshot makes adapter failure atomic even when an Attribute backend flushes
    before Django sees the raised exception.
    """
    return _attribute_snapshot(character, _LEVEL_APPLICATION_ATTRIBUTES)


def _restore_level_application_snapshot(
    character: Any, snapshot: Mapping[str, Any]
) -> None:
    """Restore a failed level application's owned state without touching XP."""
    _restore_attribute_snapshot(character, snapshot, _LEVEL_APPLICATION_ATTRIBUTES)


def _attribute_snapshot(character: Any, keys: Sequence[str]) -> dict[str, Any]:
    """Copy a bounded set of potentially mutable Evennia Attributes."""
    return {
        key: deepcopy(character.attributes.get(key))
        for key in keys
        if character.attributes.get(key) is not None
    }


def _restore_attribute_snapshot(
    character: Any, snapshot: Mapping[str, Any], keys: Sequence[str]
) -> None:
    """Restore or remove a bounded Attribute set from its primitive snapshot."""
    for key in keys:
        if key in snapshot:
            character.attributes.add(key, deepcopy(snapshot[key]))
        else:
            character.attributes.remove(key)


def _validate_character(character: Any) -> None:
    """Fail closed for non-PCs and characters already awaiting repair."""
    if getattr(character, "attributes", None) is None:
        raise AdvancementError("A persistent character is required.")
    if character.attributes.get("is_player_character") is False:
        raise AdvancementError("Only player characters can receive XP.")
    if character.attributes.get("advancement_repair_required"):
        raise AdvancementError("Character advancement requires staff repair.")
    if not CLASS_PROGRESSION.is_available(character.attributes.get("char_class")):
        _mark_repair_required(character, "missing_or_unknown_class")
        raise AdvancementError("Character advancement requires staff repair.")


def _stored_xp_and_level(character: Any) -> tuple[int, int]:
    """Read raw advancement inputs without coercing malformed legacy state."""
    xp = character.attributes.get("xp")
    level = character.attributes.get("level")
    if xp is None:
        xp = 0
    if level is None:
        level = 1
    if (
        isinstance(xp, bool)
        or not isinstance(xp, int)
        or xp < 0
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= MAX_LEVEL
    ):
        _mark_repair_required(character, "invalid_xp_or_level")
        raise AdvancementError("Character advancement requires staff repair.")
    return xp, level


def _lock_character(character: Any) -> None:
    """Acquire the character row lock used to serialize its award operations."""
    object_id = getattr(character, "pk", None)
    if not isinstance(object_id, int) or isinstance(object_id, bool) or object_id <= 0:
        raise AdvancementError("A saved character is required.")
    character.__class__.objects.select_for_update().get(pk=object_id)


def _source_digest(source_kind: str, source_id: str | int) -> str:
    """Return the display-name-independent durable identity of an award source."""
    if (
        not isinstance(source_kind, str)
        or not source_kind
        or len(source_kind) > 64
        or not source_kind.replace("_", "").replace("-", "").isalnum()
    ):
        raise AdvancementError("XP source kind is invalid.")
    if isinstance(source_id, bool) or not isinstance(source_id, (str, int)):
        raise AdvancementError("XP source identity is invalid.")
    normalized = str(source_id)
    if not normalized or len(normalized) > 160:
        raise AdvancementError("XP source identity is invalid.")
    return sha256(f"{source_kind}:{normalized}".encode()).hexdigest()


def _non_negative_integer(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdvancementError(f"{label} must be a non-negative integer.")


def _new_ledger() -> dict[str, Any]:
    return {"version": ADVANCEMENT_VERSION, "entries": [], "compacted_sources": []}


def _ledger(character: Any) -> dict[str, Any]:
    raw = character.attributes.get(ADVANCEMENT_ATTRIBUTE)
    if raw is None:
        return _new_ledger()
    if not _valid_ledger(raw):
        _mark_repair_required(character, "invalid_ledger")
        raise AdvancementError("Character advancement requires staff repair.")
    return {
        "version": ADVANCEMENT_VERSION,
        "entries": [dict(entry) for entry in raw["entries"]],
        "compacted_sources": list(raw["compacted_sources"]),
    }


def _write_ledger(character: Any, ledger: dict[str, Any]) -> None:
    if not _valid_ledger(ledger):
        raise AdvancementError("Advancement ledger cannot be persisted.")
    character.attributes.add(ADVANCEMENT_ATTRIBUTE, ledger)


def _valid_ledger(raw: Any) -> bool:
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "entries", "compacted_sources"}
        or raw["version"] != ADVANCEMENT_VERSION
        or not isinstance(raw["entries"], Sequence)
        or isinstance(raw["entries"], (str, bytes))
        or not isinstance(raw["compacted_sources"], Sequence)
        or isinstance(raw["compacted_sources"], (str, bytes))
        or len(raw["entries"]) > MAX_LEDGER_ENTRIES
        or len(raw["compacted_sources"]) > MAX_COMPACTED_SOURCES
    ):
        return False
    try:
        return all(_valid_entry(entry) for entry in raw["entries"]) and all(
            isinstance(source, str) and len(source) == 64
            for source in raw["compacted_sources"]
        )
    except (AdvancementError, KeyError, TypeError):
        return False


def _valid_entry(entry: Any) -> bool:
    return (
        isinstance(entry, Mapping)
        and set(entry) == {"source", "amount", "result"}
        and isinstance(entry["source"], str)
        and len(entry["source"]) == 64
        and isinstance(entry["amount"], int)
        and not isinstance(entry["amount"], bool)
        and entry["amount"] >= 0
        and _result_from_payload(entry["result"]) is not None
    )


def _find_entry(ledger: Mapping[str, Any], source: str) -> Mapping[str, Any] | None:
    return next(
        (entry for entry in ledger["entries"] if entry["source"] == source), None
    )


def _compact_ledger(ledger: dict[str, Any]) -> None:
    """Keep exact recent outcomes and bounded, replay-safe older identities."""
    while len(ledger["entries"]) > MAX_LEDGER_ENTRIES:
        ledger["compacted_sources"].append(ledger["entries"].pop(0)["source"])
    # A full compacted identity ring remains bounded. At saturation, a caller
    # must use ADV-06 rather than silently losing replay protection.
    if len(ledger["compacted_sources"]) > MAX_COMPACTED_SOURCES:
        raise AdvancementError("Advancement audit capacity requires staff repair.")


def _result_payload(result: AdvancementResult) -> dict[str, Any]:
    return {
        "old_xp": result.old_xp,
        "new_xp": result.new_xp,
        "old_level": result.old_level,
        "new_level": result.new_level,
        "crossed_thresholds": list(result.crossed_thresholds),
        "applied_grants": list(result.applied_grants),
        "pending_choices": list(result.pending_choices),
        "capped": result.capped,
        "reason": result.reason,
    }


def _result_from_payload(
    raw: Mapping[str, Any], *, applied: bool = True, reason: str | None = None
) -> AdvancementResult:
    if not isinstance(raw, Mapping) or set(raw) != {
        "old_xp",
        "new_xp",
        "old_level",
        "new_level",
        "crossed_thresholds",
        "applied_grants",
        "pending_choices",
        "capped",
        "reason",
    }:
        raise AdvancementError("Advancement result is invalid.")
    _non_negative_integer(raw["old_xp"], "Stored XP")
    _non_negative_integer(raw["new_xp"], "Stored XP")
    if not all(
        isinstance(raw[key], int) and 1 <= raw[key] <= MAX_LEVEL
        for key in ("old_level", "new_level")
    ):
        raise AdvancementError("Stored level is invalid.")
    for key in ("crossed_thresholds", "applied_grants", "pending_choices"):
        if not isinstance(raw[key], Sequence) or isinstance(raw[key], (str, bytes)):
            raise AdvancementError("Stored advancement result is invalid.")
    if not isinstance(raw["capped"], bool) or not isinstance(raw["reason"], str):
        raise AdvancementError("Stored advancement result is invalid.")
    return AdvancementResult(
        raw["old_xp"],
        raw["new_xp"],
        raw["old_level"],
        raw["new_level"],
        tuple(raw["crossed_thresholds"]),
        tuple(raw["applied_grants"]),
        tuple(raw["pending_choices"]),
        raw["capped"],
        applied,
        reason or raw["reason"],
    )


def progression_state(character: Any) -> Mapping[str, Any]:
    """Return a detached, validated progression-provenance snapshot.

    Read-only consumers, including ADV-06 diagnosis, use this instead of
    reverse-engineering grants from a displayed level.  A legacy record is not
    silently upgraded here: staff must make that migration explicit.
    """
    return _progression_state(character)


def expected_progression_records(
    class_key: str, level: int
) -> tuple[dict[str, Any], ...]:
    """Return the primitive occurrence records earned through ``level``.

    These records are deliberately references to stable registry keys rather
    than copies of rules prose or definitions.  They make registry drift and
    replay visible without granting anything during a read operation.
    """
    try:
        definition = CLASS_PROGRESSION.class_for(class_key)
    except RegistryValidationError as err:
        raise AdvancementError("A canonical class is required.") from err
    if (
        isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= MAX_LEVEL
    ):
        raise AdvancementError("Character level is invalid.")
    return tuple(
        record
        for earned_level in range(1, level + 1)
        for record in _level_provenance_records(definition.key, earned_level)
    )


def migrate_progression_baseline(character: Any) -> Mapping[str, Any]:
    """Explicitly migrate one supported pre-provenance character baseline.

    This is the narrow ADV-06 migration seam.  It reconstructs durable
    occurrence provenance from canonical class/XP/level inputs but never
    replays equipment, chargen menus, historical consumables, or unresolved
    player selections.  Idempotent feature ownership is restored only for
    released automatic actions; resource initialization is equally explicit.
    """
    with transaction.atomic():
        _lock_character(character)
        xp, level = _stored_xp_and_level(character)
        if earned_level(xp) != level:
            _mark_repair_required(character, "level_xp_mismatch")
            raise AdvancementError("Character advancement requires staff repair.")
        class_key = character.attributes.get("char_class")
        try:
            definition = CLASS_PROGRESSION.class_for(class_key)
        except RegistryValidationError as err:
            _mark_repair_required(character, "missing_or_unknown_class")
            raise AdvancementError(
                "Character advancement requires staff repair."
            ) from err
        raw = character.attributes.get(PROGRESSION_ATTRIBUTE)
        if raw is not None and _valid_progression_state(raw):
            if (
                raw["registry_version"] == CLASS_PROGRESSION.version
                and raw["fingerprint"] == CLASS_PROGRESSION.fingerprint
                and raw["class_key"] == definition.key
            ):
                return _progression_state(character)
        state = _new_progression_state(definition.key, through_level=level)
        _write_progression_state(character, state)
        for earned in range(1, level + 1):
            grants = definition.grants_at(earned)
            _grant_automatic_actions(character, grants)
            _initialize_granted_resources(character, grants)
            _initialize_spell_access(character, grants)
        character.attributes.remove("advancement_repair_required")
        return _progression_state(character)


def _new_progression_state(class_key: str, *, through_level: int) -> dict[str, Any]:
    """Create a full primitive provenance baseline for a known class level."""
    return {
        "version": PROGRESSION_VERSION,
        "class_key": class_key,
        "registry_version": CLASS_PROGRESSION.version,
        "fingerprint": CLASS_PROGRESSION.fingerprint,
        "levels": [
            {
                "level": level,
                "records": _level_provenance_records(class_key, level),
            }
            for level in range(1, through_level + 1)
        ],
    }


def _progression_state(character: Any) -> dict[str, Any]:
    """Load provenance without accepting legacy or drifted state as current."""
    raw = character.attributes.get(PROGRESSION_ATTRIBUTE)
    if not _valid_progression_state(raw):
        _mark_repair_required(character, "invalid_or_legacy_progression")
        raise AdvancementError("Character advancement requires staff repair.")
    state = {
        "version": raw["version"],
        "class_key": raw["class_key"],
        "registry_version": raw["registry_version"],
        "fingerprint": raw["fingerprint"],
        "levels": [
            {
                "level": item["level"],
                "records": [dict(record) for record in item["records"]],
            }
            for item in raw["levels"]
        ],
    }
    if (
        state["registry_version"] != CLASS_PROGRESSION.version
        or state["fingerprint"] != CLASS_PROGRESSION.fingerprint
    ):
        _mark_repair_required(character, "progression_version_drift")
        raise AdvancementError("Character advancement requires staff repair.")
    expected = _new_progression_state(
        state["class_key"], through_level=len(state["levels"])
    )
    if state["levels"] != expected["levels"]:
        _mark_repair_required(character, "progression_provenance_mismatch")
        raise AdvancementError("Character advancement requires staff repair.")
    return state


def _write_progression_state(character: Any, state: Mapping[str, Any]) -> None:
    """Persist one validated provenance snapshot without mutable references."""
    if not _valid_progression_state(state):
        raise AdvancementError("Progression provenance cannot be persisted.")
    character.attributes.add(PROGRESSION_ATTRIBUTE, dict(state))


def _append_level_provenance(state: dict[str, Any], class_key: str, level: int) -> None:
    """Append one exact level occurrence or reject an attempted replay."""
    existing = next((item for item in state["levels"] if item["level"] == level), None)
    expected = _level_provenance_records(class_key, level)
    if existing is not None:
        if existing["records"] != expected:
            raise AdvancementError("Character advancement requires staff repair.")
        return
    if level != len(state["levels"]) + 1:
        raise AdvancementError("Character advancement requires staff repair.")
    state["levels"].append({"level": level, "records": expected})


def _level_provenance_records(class_key: str, level: int) -> list[dict[str, Any]]:
    """Build stable records for every released grant entitlement at one level."""
    definition = CLASS_PROGRESSION.class_for(class_key)
    grants = definition.grants_at(level)
    records: list[dict[str, Any]] = []
    for kind, keys in (
        ("feature", grants.automatic_feature_keys),
        ("resource", grants.resource_keys),
        ("spell_access", grants.spell_access_keys),
        ("choice", grants.choice_keys),
    ):
        for key in keys:
            records.append(
                {
                    "id": f"{class_key.casefold()}:{level}:{kind}:{key}",
                    "kind": kind,
                    "key": key,
                    "level": level,
                    "registry_version": CLASS_PROGRESSION.version,
                    "registry_fingerprint": CLASS_PROGRESSION.fingerprint,
                }
            )
    return records


def _valid_progression_state(raw: Any) -> bool:
    """Validate bounded primitive provenance before any advancement mutation."""
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {"version", "class_key", "registry_version", "fingerprint", "levels"}
        or raw["version"] != PROGRESSION_VERSION
        or not isinstance(raw["class_key"], str)
        or isinstance(raw["registry_version"], bool)
        or not isinstance(raw["registry_version"], int)
        or not isinstance(raw["fingerprint"], str)
        or len(raw["fingerprint"]) != 64
        or not isinstance(raw["levels"], Sequence)
        or isinstance(raw["levels"], (str, bytes))
        or not 1 <= len(raw["levels"]) <= MAX_LEVEL
    ):
        return False
    for expected_level, item in enumerate(raw["levels"], start=1):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"level", "records"}
            or item["level"] != expected_level
            or not isinstance(item["records"], Sequence)
            or isinstance(item["records"], (str, bytes))
            or any(
                not _valid_provenance_record(record, expected_level, raw)
                for record in item["records"]
            )
        ):
            return False
    return True


def _valid_provenance_record(record: Any, level: int, state: Mapping[str, Any]) -> bool:
    """Validate one self-contained primitive occurrence without live inference."""
    return (
        isinstance(record, Mapping)
        and set(record)
        == {"id", "kind", "key", "level", "registry_version", "registry_fingerprint"}
        and isinstance(record["id"], str)
        and isinstance(record["key"], str)
        and record["kind"] in {"feature", "resource", "spell_access", "choice"}
        and record["level"] == level
        and isinstance(record["registry_version"], int)
        and isinstance(record["registry_fingerprint"], str)
        and len(record["registry_fingerprint"]) == 64
    )


def _mark_repair_required(character: Any, reason: str) -> None:
    """Persist a minimal, inspectable quarantine without altering XP or level."""
    character.db.advancement_repair_required = reason

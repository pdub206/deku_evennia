"""ADV-01's canonical, transactional experience and level service.

The module deliberately owns only XP, level, and the fixed hit-point gain
available before ADV-02's alpha registry is complete. Callers must use a stable
source identity; a duplicate identity is a durable no-op.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from string import hexdigits
from typing import Any

from django.db import transaction
from systems.progression import CLASS_PROGRESSION, RegistryValidationError

SRD_MAX_LEVEL = 20
RELEASE_LEVEL_CAP = 3
# Compatibility for callers that mean the highest presently attainable level.
MAX_LEVEL = RELEASE_LEVEL_CAP
MAX_LEDGER_ENTRIES = 128
MAX_COMPACTED_SOURCES = 4096
ADVANCEMENT_ATTRIBUTE = "advancement_ledger"
ADVANCEMENT_VERSION = 2

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


class AdvancementSnapshotError(AdvancementError):
    """Expose one bounded diagnostic code from read-only validation."""

    def __init__(self, diagnostic: str):
        super().__init__("Character advancement requires staff repair.")
        self.diagnostic = diagnostic


class _RepairRequired(AdvancementError):
    """Carry an inspectable quarantine reason out of a rolled-back transaction."""

    def __init__(self, reason: str):
        super().__init__("Character advancement requires staff repair.")
        self.reason = reason


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


@dataclass(frozen=True)
class AdvancementStateSnapshot:
    """Validated, read-only identity for player and staff presentation."""

    xp: int
    level: int
    class_key: str
    registry_version: int
    registry_fingerprint: str
    grants: tuple[str, ...]
    ledger_version: int
    last_award_source: str | None
    repair_required: str | None


def earned_level(xp: int) -> int:
    """Return the uncapped SRD level earned by a cumulative XP total."""
    _non_negative_integer(xp, "XP")
    return min(SRD_MAX_LEVEL, sum(xp >= threshold for threshold in XP_THRESHOLDS))


def effective_level(xp: int) -> int:
    """Return the level currently attainable under the alpha release cap."""
    return min(RELEASE_LEVEL_CAP, earned_level(xp))


def advancement_state_snapshot(character: Any) -> AdvancementStateSnapshot:
    """Validate advancement state without repairing, granting, or consuming it."""
    repair = character.attributes.get("advancement_repair_required")
    if repair is not None and not isinstance(repair, str):
        raise AdvancementSnapshotError("invalid_repair_marker")
    try:
        _validate_character(character)
        xp, level = _stored_xp_and_level(character)
        if effective_level(xp) != level:
            raise _RepairRequired("level_xp_mismatch")
        progression = _progression_state(character, level)
        ledger = _ledger(character)
    except _RepairRequired as err:
        raise AdvancementSnapshotError(err.reason) from err
    except AdvancementError as err:
        diagnostic = repair if isinstance(repair, str) else "invalid_advancement_state"
        raise AdvancementSnapshotError(diagnostic) from err
    entries = ledger["entries"]
    return AdvancementStateSnapshot(
        xp,
        level,
        progression["class_key"],
        progression["registry_version"],
        progression["fingerprint"],
        tuple(progression["grants"]),
        ledger["version"],
        entries[-1]["source"] if entries else None,
        repair,
    )


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
    try:
        with transaction.atomic():
            _lock_character(character)
            _validate_character(character)
            old_xp, stored_level = _stored_xp_and_level(character)
            old_level = effective_level(old_xp)
            if stored_level != old_level:
                raise _RepairRequired("level_xp_mismatch")
            _progression_state(character, old_level)

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
                    old_xp,
                    old_xp,
                    old_level,
                    old_level,
                    (),
                    (),
                    (),
                    old_level == RELEASE_LEVEL_CAP,
                    False,
                    "compacted_source",
                )

            new_xp = old_xp + amount
            new_level = effective_level(new_xp)
            missing_hp = character.stats.hp_max - character.stats.hp_current
            crossed = tuple(XP_THRESHOLDS[old_level:new_level])
            grants, pending_choices = _apply_levels(character, old_level, new_level)
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
                new_level == RELEASE_LEVEL_CAP,
                True,
                "awarded" if amount else "zero_award",
            )
            ledger["entries"].append(
                {"source": source, "amount": amount, "result": _result_payload(result)}
            )
            _compact_ledger(ledger)
            _write_ledger(character, ledger)
    except _RepairRequired as err:
        _mark_repair_required(character, err.reason)
        raise AdvancementError("Character advancement requires staff repair.") from err
    except Exception:
        # Django rolls persistent writes back, but Evennia's in-process
        # Attribute cache must also forget values written inside the failed
        # transaction.
        _discard_attribute_cache(character)
        raise
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
        character.db.char_class = class_key
        character.db.xp = 0
        character.db.level = 1
        character.db.hp_base = max(1, hp_base)
        character.db.class_progression = {
            "class_key": definition.key,
            "registry_version": CLASS_PROGRESSION.version,
            "fingerprint": CLASS_PROGRESSION.fingerprint,
            "grants": list(definition.grants_at(1).automatic_feature_keys),
        }
        # ADV-03 owns the durable record; ADV-01 only creates earned choice
        # entitlements and deliberately never selects an option itself.
        from systems.training import initialize_choice_entitlements

        initialize_choice_entitlements(character, definition.key, 1)
        _write_ledger(character, _new_ledger())


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
    # CharacterStats applies Constitution once per current level. Store only
    # the class basis here, increasing it when necessary to preserve the SRD's
    # minimum one total HP gained per level for an extreme negative modifier.
    basis_gain = max(definition.fixed_hp_gain, 1 - constitution)
    character.db.hp_base = character.stats.hp_base + basis_gain * (
        new_level - old_level
    )
    from systems.training import initialize_choice_entitlements

    pending: list[str] = []
    applied: list[str] = []
    progression = _progression_state(character, old_level)
    for level in range(old_level + 1, new_level + 1):
        level_grants = definition.grants_at(level)
        choices = level_grants.choice_keys
        initialize_choice_entitlements(character, definition.key, level)
        pending.extend(choices)
        applied.append(f"hp_level_{level}")
        applied.extend(level_grants.automatic_feature_keys)
        progression["grants"].extend(level_grants.automatic_feature_keys)
    character.db.class_progression = progression
    return applied, tuple(pending)


def _preserve_missing_hp(character: Any, missing_hp: int) -> None:
    """Keep damage taken constant when a level increases maximum HP."""
    # hp_base and level have already changed.  Deliberately write the resource
    # directly: leveling must not emit combat-policy effects before commit.
    character.db.hp_current = max(0, character.stats.hp_max - missing_hp)


def _validate_character(character: Any) -> None:
    """Fail closed for non-PCs and characters already awaiting repair."""
    if getattr(character, "attributes", None) is None:
        raise AdvancementError("A persistent character is required.")
    if character.attributes.get("is_player_character") is False:
        raise AdvancementError("Only player characters can receive XP.")
    if character.attributes.get("advancement_repair_required"):
        raise AdvancementError("Character advancement requires staff repair.")
    if not CLASS_PROGRESSION.is_available(character.attributes.get("char_class")):
        raise _RepairRequired("missing_or_unknown_class")


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
        or not 1 <= level <= RELEASE_LEVEL_CAP
    ):
        raise _RepairRequired("invalid_xp_or_level")
    return xp, level


def _progression_state(character: Any, level: int) -> dict[str, Any]:
    """Return a safe mutable copy of the character's grant provenance."""
    raw = character.attributes.get("class_progression")
    class_key = character.attributes.get("char_class")
    try:
        definition = CLASS_PROGRESSION.class_for(class_key)
        expected_grants = [
            key
            for grants in definition.levels[:level]
            for key in grants.automatic_feature_keys
        ]
    except (RegistryValidationError, TypeError):
        raise _RepairRequired("invalid_class_progression") from None
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"class_key", "registry_version", "fingerprint", "grants"}
        or raw["class_key"] != class_key
        or raw["registry_version"] != CLASS_PROGRESSION.version
        or raw["fingerprint"] != CLASS_PROGRESSION.fingerprint
        or not isinstance(raw["grants"], Sequence)
        or isinstance(raw["grants"], (str, bytes))
        or any(not isinstance(key, str) or not key for key in raw["grants"])
        or list(raw["grants"]) != expected_grants
    ):
        raise _RepairRequired("invalid_class_progression")
    return {
        "class_key": raw["class_key"],
        "registry_version": raw["registry_version"],
        "fingerprint": raw["fingerprint"],
        "grants": list(raw["grants"]),
    }


def _lock_character(character: Any) -> None:
    """Acquire the character row lock used to serialize its award operations."""
    object_id = getattr(character, "pk", None)
    if not isinstance(object_id, int) or isinstance(object_id, bool) or object_id <= 0:
        raise AdvancementError("A saved character is required.")
    character.__class__.objects.select_for_update().get(pk=object_id)
    # The caller may have populated Evennia's aggressive Attribute cache before
    # waiting for this lock. Force all subsequent reads to see the winner's
    # committed state rather than applying an award to that stale snapshot.
    _discard_attribute_cache(character)


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
        raise _RepairRequired("invalid_ledger")
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
        entry_sources = [entry["source"] for entry in raw["entries"]]
        compacted = list(raw["compacted_sources"])
        return (
            all(_valid_entry(entry) for entry in raw["entries"])
            and all(_valid_digest(source) for source in compacted)
            and len(set(entry_sources)) == len(entry_sources)
            and len(set(compacted)) == len(compacted)
            and not set(entry_sources).intersection(compacted)
        )
    except (AdvancementError, KeyError, TypeError):
        return False


def _valid_entry(entry: Any) -> bool:
    if not (
        isinstance(entry, Mapping)
        and set(entry) == {"source", "amount", "result"}
        and _valid_digest(entry["source"])
        and isinstance(entry["amount"], int)
        and not isinstance(entry["amount"], bool)
        and entry["amount"] >= 0
    ):
        return False
    result = _result_from_payload(entry["result"])
    return result.new_xp - result.old_xp == entry["amount"]


def _valid_digest(value: Any) -> bool:
    """Return whether a stored source identity is a complete SHA-256 digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in hexdigits for character in value)
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
        raise _RepairRequired("ledger_capacity")


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
        isinstance(raw[key], int)
        and not isinstance(raw[key], bool)
        and 1 <= raw[key] <= RELEASE_LEVEL_CAP
        for key in ("old_level", "new_level")
    ):
        raise AdvancementError("Stored level is invalid.")
    for key in ("crossed_thresholds", "applied_grants", "pending_choices"):
        if not isinstance(raw[key], Sequence) or isinstance(raw[key], (str, bytes)):
            raise AdvancementError("Stored advancement result is invalid.")
    if (
        raw["new_xp"] < raw["old_xp"]
        or raw["new_level"] < raw["old_level"]
        or effective_level(raw["old_xp"]) != raw["old_level"]
        or effective_level(raw["new_xp"]) != raw["new_level"]
        or tuple(raw["crossed_thresholds"])
        != XP_THRESHOLDS[raw["old_level"] : raw["new_level"]]
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in raw["crossed_thresholds"]
        )
        or any(
            not isinstance(value, str) or not value
            for key in ("applied_grants", "pending_choices")
            for value in raw[key]
        )
    ):
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


def _mark_repair_required(character: Any, reason: str) -> None:
    """Persist a minimal, inspectable quarantine without altering XP or level."""
    _discard_attribute_cache(character)
    character.db.advancement_repair_required = reason


def _discard_attribute_cache(character: Any) -> None:
    """Discard idmapper values that may outlive a database rollback or lock wait."""
    for attribute in character.attributes.all():
        attribute.flush_from_cache(force=True)
    character.attributes.reset_cache()

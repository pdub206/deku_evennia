"""ADV-01's canonical, transactional experience and level service.

The module deliberately owns only XP, level, and the fixed hit-point gain
available before ADV-02's progression registry exists.  Callers must use a
stable source identity; a duplicate identity is a durable no-op.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from django.db import transaction
from systems.progression import CLASS_PROGRESSION, RegistryValidationError

MAX_LEVEL = 20
MAX_LEDGER_ENTRIES = 128
MAX_COMPACTED_SOURCES = 4096
ADVANCEMENT_ATTRIBUTE = "advancement_ledger"
ADVANCEMENT_VERSION = 1

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
        missing_hp = character.stats.hp_max - character.stats.hp_current
        crossed = tuple(
            threshold
            for threshold in XP_THRESHOLDS[old_level:new_level]
            if threshold <= new_xp
        )
        grants = _apply_levels(character, old_level, new_level)
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
            (),
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
        _write_ledger(character, _new_ledger())


def _apply_levels(character: Any, old_level: int, new_level: int) -> list[str]:
    """Apply the registry-defined fixed HP grant for each crossed level."""
    if new_level <= old_level:
        return []
    try:
        definition = CLASS_PROGRESSION.class_for(character.attributes.get("char_class"))
    except RegistryValidationError as err:
        raise AdvancementError("Character advancement requires staff repair.") from err
    constitution = character.stats.ability_modifier("Constitution")
    gain = max(1, definition.fixed_hp_gain + constitution)
    character.db.hp_base = character.stats.hp_base + gain * (new_level - old_level)
    return tuple(f"hp_level_{level}" for level in range(old_level + 1, new_level + 1))


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


def _mark_repair_required(character: Any, reason: str) -> None:
    """Persist a minimal, inspectable quarantine without altering XP or level."""
    character.db.advancement_repair_required = reason

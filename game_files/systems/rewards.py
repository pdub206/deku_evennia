"""COMBAT-07 kill attribution and NPC experience rewards.

The injury service owns the irreversible death identity.  This module consumes
that identity once, using a primitive per-victim contribution ledger so later
systems (groups, pets, and environmental damage) share one attribution result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from evennia.server.models import ServerConfig
from evennia.utils import logger
from systems.combat_outcomes import calculate_npc_xp
from systems.injury import InjuryError, InjuryState, injury_record

LEDGER_ATTRIBUTE = "combat_contribution_ledger"
REWARDS_CONFIG_KEY = "combat_reward_results"
LEDGER_VERSION = REWARDS_VERSION = 1
MAX_NPC_XP_REWARD = 1_000_000


class RewardError(ValueError):
    """Raised when durable reward data is malformed."""


@dataclass(frozen=True)
class Contribution:
    """One primitive, ordered attribution candidate."""

    source_id: int | None
    source_kind: str
    responsible_id: int | None
    order: int
    encounter_id: int | None


@dataclass(frozen=True)
class RewardResult:
    """The immutable resolution of one final-death identity."""

    death_id: str
    victim_id: int
    victim_name: str
    source_id: int | None
    candidates: tuple[Contribution, ...]
    rejected: tuple[tuple[int | None, str], ...]
    credited_id: int | None
    base_xp: int | None
    npc_level: int | None
    recipient_level: int | None
    multiplier: float | None
    final_xp: int
    previous_xp: int | None
    resulting_xp: int | None
    reason: str
    consumed: bool = True


def record_damage(
    victim: Any, source: Any | None, *, source_kind: str = "damage"
) -> Contribution | None:
    """Append one eligible damage source before it can become a later death.

    A source need not be a PC.  Its owner is resolved now and only primitive
    dbrefs are persisted, preventing reloads from retaining live objects.
    """
    if not _is_npc(victim):
        return None
    source_id = _object_id(source)
    responsible_id = _responsible_pc_id(source)
    if source is None and source_kind == "damage":
        source_kind = "environment"
    if not isinstance(source_kind, str) or not source_kind or len(source_kind) > 40:
        raise RewardError("Contribution source kind is invalid.")
    ledger = _ledger(victim)
    encounter_id = _encounter_id(victim)
    if not ledger["open"] or ledger["encounter_id"] != encounter_id:
        ledger = _new_ledger(encounter_id)
    contribution = Contribution(
        source_id, source_kind, responsible_id, ledger["next_order"], encounter_id
    )
    ledger["contributions"].append(_contribution_payload(contribution))
    ledger["next_order"] += 1
    _write_ledger(victim, ledger)
    return contribution


def close_encounter_ledger(encounter_id: int) -> None:
    """Close matching ledgers so old damage cannot credit a later encounter."""
    if not _positive_int(encounter_id):
        return
    from typeclasses.characters import Character

    for victim in Character.objects.filter_family().iterator():
        try:
            ledger = _ledger(victim)
            if ledger["open"] and ledger["encounter_id"] == encounter_id:
                ledger["open"] = False
                _write_ledger(victim, ledger)
        except Exception:
            logger.log_trace(
                f"Could not close COMBAT-07 ledger for object #{getattr(victim, 'id', '?')}."
            )


def resolve_death(
    victim: Any, death_id: str, *, source: Any | None = None
) -> RewardResult:
    """Consume one death identity and make its sole NPC XP transaction.

    The XP Attribute and audit record are written in one database transaction.
    A repeat callback reads the saved immutable result and never sends a second
    payout message.
    """
    if not isinstance(death_id, str) or not death_id:
        raise RewardError("A reward requires a non-empty death identity.")
    with transaction.atomic():
        stored = _result_store()
        existing = stored["results"].get(death_id)
        if existing is not None:
            return _result_from_payload(existing)

        result, recipient = _resolve(victim, death_id, source)
        if recipient is not None and result.final_xp:
            recipient.stats.set_xp(result.resulting_xp or 0)
        stored["results"][death_id] = _result_payload(result)
        _write_result_store(stored)
        if _is_npc(victim):
            ledger = _ledger(victim)
            ledger["open"] = False
            _write_ledger(victim, ledger)
    if recipient is not None and result.final_xp:
        recipient.msg(
            f"You defeat {victim.key}: base {result.base_xp} XP, "
            f"level adjustment {result.multiplier:.0%}, gain {result.final_xp} XP."
        )
    return result


def reward_result(death_id: str) -> RewardResult | None:
    """Return lock-gatable staff diagnostics for a consumed death identity."""
    stored = _result_store()
    raw = stored["results"].get(death_id)
    return _result_from_payload(raw) if raw is not None else None


def _resolve(
    victim: Any, death_id: str, source: Any | None
) -> tuple[RewardResult, Any | None]:
    """Evaluate direct attribution then newest recorded contributors."""
    victim_id = _object_id(victim) or 0
    if not _is_npc(victim):
        return (
            _no_reward(
                death_id, victim, (), (), "pvp_or_non_npc", source_id=_object_id(source)
            ),
            None,
        )
    try:
        if injury_record(victim).state is not InjuryState.DEAD:
            return (
                _no_reward(
                    death_id,
                    victim,
                    (),
                    (),
                    "victim_not_dead",
                    source_id=_object_id(source),
                ),
                None,
            )
    except InjuryError:
        return (
            _no_reward(
                death_id, victim, (), (), "invalid_injury", source_id=_object_id(source)
            ),
            None,
        )

    direct = (
        Contribution(
            _object_id(source),
            "direct",
            _responsible_pc_id(source),
            1,
            _encounter_id(victim),
        )
        if source is not None
        else None
    )
    ledger = _ledger(victim)
    history = tuple(_contribution_from_payload(raw) for raw in ledger["contributions"])
    candidates = tuple(
        candidate for candidate in (direct, *reversed(history)) if candidate
    )
    rejected: list[tuple[int | None, str]] = []
    recipient = None
    for candidate in candidates:
        if candidate.responsible_id is None:
            rejected.append((None, "no_responsible_pc"))
            continue
        recipient, reason = _eligible_recipient(candidate.responsible_id, victim)
        if recipient is not None:
            break
        rejected.append((candidate.responsible_id, reason))
    if recipient is None:
        return (
            _no_reward(
                death_id,
                victim,
                candidates,
                tuple(rejected),
                "no_eligible_recipient",
                source_id=_object_id(source),
            ),
            None,
        )

    base = victim.attributes.get("xp_reward")
    if (
        isinstance(base, bool)
        or not isinstance(base, int)
        or not 0 <= base <= MAX_NPC_XP_REWARD
    ):
        return (
            _no_reward(
                death_id,
                victim,
                candidates,
                tuple(rejected),
                "invalid_xp_reward",
                source_id=_object_id(source),
            ),
            None,
        )
    npc_level, recipient_level = victim.stats.level, recipient.stats.level
    multiplier, final_xp = calculate_npc_xp(base, npc_level, recipient_level)
    previous_xp = recipient.stats.xp
    return (
        RewardResult(
            death_id,
            victim_id,
            victim.key,
            _object_id(source),
            candidates,
            tuple(rejected),
            recipient.id,
            base,
            npc_level,
            recipient_level,
            multiplier,
            final_xp,
            previous_xp,
            previous_xp + final_xp,
            "awarded",
        ),
        recipient,
    )


def _no_reward(
    death_id: str,
    victim: Any,
    candidates: tuple[Contribution, ...],
    rejected: tuple[tuple[int | None, str], ...],
    reason: str,
    *,
    source_id: int | None = None,
) -> RewardResult:
    """Build an auditable consumed result which does not mutate XP."""
    base = victim.attributes.get("xp_reward") if _is_npc(victim) else None
    if (
        isinstance(base, bool)
        or not isinstance(base, int)
        or not 0 <= base <= MAX_NPC_XP_REWARD
    ):
        base = None
    return RewardResult(
        death_id,
        _object_id(victim) or 0,
        getattr(victim, "key", "unknown"),
        source_id,
        candidates,
        rejected,
        None,
        base,
        victim.stats.level if _is_npc(victim) else None,
        None,
        None,
        0,
        None,
        None,
        reason,
    )


def _eligible_recipient(character_id: int, victim: Any) -> tuple[Any | None, str]:
    """Validate the exact death-time eligibility rules for a candidate PC."""
    character = _character(character_id)
    if character is None:
        return None, "missing"
    if not _is_pc(character):
        return None, "not_player_character"
    if character.location is None or character.location is not victim.location:
        return None, "remote"
    try:
        state = injury_record(character).state
    except InjuryError:
        return None, "invalid_injury"
    if state is not InjuryState.CONSCIOUS or character.stats.hp_current <= 0:
        return None, "unconscious_or_dead"
    if getattr(character.action_position, "value", None) == "sleeping":
        return None, "sleeping"
    return character, ""


def _is_pc(character: Any) -> bool:
    """Identify PCs from durable ownership rather than session presence."""
    return (
        getattr(character, "attributes", None) is not None
        and character.attributes.get("is_player_character") is not False
    )


def _is_npc(character: Any) -> bool:
    """NPCs are the only death victims which can issue XP."""
    return (
        getattr(character, "attributes", None) is not None
        and character.attributes.get("is_player_character") is False
    )


def _responsible_pc_id(source: Any | None) -> int | None:
    """Map a direct PC or a controlled creature to its responsible PC dbref."""
    if source is None:
        return None
    if _is_pc(source):
        return _object_id(source)
    # MOB-07 snapshots the current controller/owner at damage time.  Later
    # charm expiry or transfer cannot rewrite this ledger entry.
    try:
        from systems.mobile_relationships import responsible_pc_id

        responsible = responsible_pc_id(source)
        if responsible is not None:
            return responsible
    except Exception:
        pass
    for key in ("owner_character_id", "owner_id", "controller_id"):
        candidate = (
            source.attributes.get(key) if getattr(source, "attributes", None) else None
        )
        if _positive_int(candidate):
            owner = _character(candidate)
            if owner is not None and _is_pc(owner):
                return candidate
    owner = getattr(getattr(source, "db", None), "owner", None)
    owner_id = _object_id(owner)
    return owner_id if owner_id and _is_pc(owner) else None


def _encounter_id(victim: Any) -> int | None:
    """Read the current combat encounter without duplicating registry access."""
    try:
        from systems.combat import get_encounter_id

        return get_encounter_id(victim)
    except Exception:
        return None


def _ledger(victim: Any) -> dict[str, Any]:
    raw = victim.attributes.get(LEDGER_ATTRIBUTE)
    if raw is None:
        ledger = _new_ledger(_encounter_id(victim))
        _write_ledger(victim, ledger)
        return ledger
    if not _valid_ledger(raw):
        raise RewardError("Contribution ledger is invalid.")
    return {
        "version": LEDGER_VERSION,
        "open": raw["open"],
        "encounter_id": raw["encounter_id"],
        "next_order": raw["next_order"],
        "contributions": [dict(item) for item in raw["contributions"]],
    }


def _new_ledger(encounter_id: int | None) -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "open": True,
        "encounter_id": encounter_id,
        "next_order": 1,
        "contributions": [],
    }


def _write_ledger(victim: Any, ledger: dict[str, Any]) -> None:
    if not _valid_ledger(ledger):
        raise RewardError("Contribution ledger cannot be persisted.")
    victim.attributes.add(LEDGER_ATTRIBUTE, ledger)


def _valid_ledger(raw: Any) -> bool:
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {"version", "open", "encounter_id", "next_order", "contributions"}
        or raw["version"] != LEDGER_VERSION
        or not isinstance(raw["open"], bool)
        or (raw["encounter_id"] is not None and not _positive_int(raw["encounter_id"]))
        or not _positive_int(raw["next_order"])
        or not isinstance(raw["contributions"], Sequence)
        or isinstance(raw["contributions"], (str, bytes))
    ):
        return False
    try:
        contributions = [
            _contribution_from_payload(item) for item in raw["contributions"]
        ]
    except RewardError:
        return False
    return [item.order for item in contributions] == list(range(1, raw["next_order"]))


def _contribution_payload(item: Contribution) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source_kind": item.source_kind,
        "responsible_id": item.responsible_id,
        "order": item.order,
        "encounter_id": item.encounter_id,
    }


def _contribution_from_payload(raw: Any) -> Contribution:
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {"source_id", "source_kind", "responsible_id", "order", "encounter_id"}
        or (raw["source_id"] is not None and not _positive_int(raw["source_id"]))
        or not isinstance(raw["source_kind"], str)
        or not raw["source_kind"]
        or (
            raw["responsible_id"] is not None
            and not _positive_int(raw["responsible_id"])
        )
        or not _positive_int(raw["order"])
        or (raw["encounter_id"] is not None and not _positive_int(raw["encounter_id"]))
    ):
        raise RewardError("Contribution entry is invalid.")
    return Contribution(
        raw["source_id"],
        raw["source_kind"],
        raw["responsible_id"],
        raw["order"],
        raw["encounter_id"],
    )


def _result_store() -> dict[str, Any]:
    raw = ServerConfig.objects.conf(REWARDS_CONFIG_KEY)
    if raw is None:
        return {"version": REWARDS_VERSION, "results": {}}
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "results"}
        or raw["version"] != REWARDS_VERSION
        or not isinstance(raw["results"], Mapping)
    ):
        raise RewardError("Reward audit storage is invalid.")
    return {"version": REWARDS_VERSION, "results": dict(raw["results"])}


def _write_result_store(store: dict[str, Any]) -> None:
    ServerConfig.objects.conf(REWARDS_CONFIG_KEY, value=store)


def _result_payload(result: RewardResult) -> dict[str, Any]:
    return {
        "death_id": result.death_id,
        "victim_id": result.victim_id,
        "victim_name": result.victim_name,
        "source_id": result.source_id,
        "candidates": [_contribution_payload(item) for item in result.candidates],
        "rejected": [[candidate, reason] for candidate, reason in result.rejected],
        "credited_id": result.credited_id,
        "base_xp": result.base_xp,
        "npc_level": result.npc_level,
        "recipient_level": result.recipient_level,
        "multiplier": result.multiplier,
        "final_xp": result.final_xp,
        "previous_xp": result.previous_xp,
        "resulting_xp": result.resulting_xp,
        "reason": result.reason,
    }


def _result_from_payload(raw: Any) -> RewardResult:
    if not isinstance(raw, Mapping):
        raise RewardError("Reward result is invalid.")
    candidates = tuple(_contribution_from_payload(item) for item in raw["candidates"])
    rejected = tuple((item[0], item[1]) for item in raw["rejected"])
    return RewardResult(
        raw["death_id"],
        raw["victim_id"],
        raw["victim_name"],
        raw["source_id"],
        candidates,
        rejected,
        raw["credited_id"],
        raw["base_xp"],
        raw["npc_level"],
        raw["recipient_level"],
        raw["multiplier"],
        raw["final_xp"],
        raw["previous_xp"],
        raw["resulting_xp"],
        raw["reason"],
    )


def _character(object_id: int) -> Any | None:
    try:
        from typeclasses.characters import Character

        return Character.objects.get(id=object_id)
    except Exception:
        return None


def _object_id(value: Any | None) -> int | None:
    object_id = getattr(value, "id", None)
    return object_id if _positive_int(object_id) else None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

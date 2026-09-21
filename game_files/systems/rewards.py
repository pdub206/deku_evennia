"""COMBAT-07 kill attribution and NPC experience rewards.

The injury service owns the irreversible death identity.  This module consumes
that identity once, using a primitive per-victim contribution ledger so later
systems (groups, pets, and environmental damage) share one attribution result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.db import transaction
from evennia.server.models import ServerConfig
from evennia.utils import logger
from systems.combat_outcomes import calculate_npc_xp
from systems.injury import InjuryError, InjuryState, injury_record

LEDGER_ATTRIBUTE = "combat_contribution_ledger"
REWARDS_CONFIG_KEY = "combat_reward_results"
LEDGER_VERSION = REWARDS_VERSION = 2
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
    group_id: int | None = None
    membership_sequence: int | None = None
    roster_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class RewardShare:
    """One immutable member payout from a solo or frozen group reward."""

    recipient_id: int
    raw_xp: int
    recipient_level: int
    multiplier: float
    final_xp: int
    previous_xp: int | None
    resulting_xp: int | None


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
    group_id: int | None = None
    membership_sequence: int | None = None
    frozen_roster: tuple[int, ...] = ()
    shares: tuple[RewardShare, ...] = ()


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
    contribution = _contribution(
        source_id, source_kind, responsible_id, ledger["next_order"], encounter_id
    )
    if contribution.group_id is not None:
        prior = next(
            (
                _contribution_from_payload(raw)
                for raw in ledger["contributions"]
                if raw["group_id"] == contribution.group_id
            ),
            None,
        )
        if prior is not None:
            contribution = replace(
                contribution,
                membership_sequence=prior.membership_sequence,
                roster_ids=prior.roster_ids,
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

        result, recipients = _resolve(victim, death_id, source)
        shares: list[RewardShare] = []
        for recipient, share in recipients:
            if not share.final_xp:
                shares.append(share)
                continue
            from systems.advancement import award_xp

            advancement = award_xp(
                recipient,
                share.final_xp,
                source_kind="combat_death",
                source_id=f"{death_id}:{recipient.id}",
            )
            if not advancement.applied:
                raise RewardError("A new death identity has an existing XP award.")
            shares.append(
                replace(
                    share,
                    previous_xp=advancement.old_xp,
                    resulting_xp=advancement.new_xp,
                )
            )
        if shares:
            result = replace(
                result,
                shares=tuple(shares),
                previous_xp=shares[0].previous_xp,
                resulting_xp=shares[0].resulting_xp,
            )
        stored["results"][death_id] = _result_payload(result)
        _write_result_store(stored)
        if _is_npc(victim):
            ledger = _ledger(victim)
            ledger["open"] = False
            _write_ledger(victim, ledger)
    for recipient, share in recipients:
        if share.final_xp:
            recipient.msg(
                f"You defeat {victim.key}: base share {share.raw_xp} XP, "
                f"level adjustment {share.multiplier:.0%}, gain {share.final_xp} XP."
            )
    return result


def reward_result(death_id: str) -> RewardResult | None:
    """Return lock-gatable staff diagnostics for a consumed death identity."""
    stored = _result_store()
    raw = stored["results"].get(death_id)
    return _result_from_payload(raw) if raw is not None else None


def _resolve(
    victim: Any, death_id: str, source: Any | None
) -> tuple[RewardResult, tuple[tuple[Any, RewardShare], ...]]:
    """Evaluate direct attribution then newest recorded contributors."""
    victim_id = _object_id(victim) or 0
    if not _is_npc(victim):
        return (
            _no_reward(
                death_id, victim, (), (), "pvp_or_non_npc", source_id=_object_id(source)
            ),
            (),
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
                (),
            )
    except InjuryError:
        return (
            _no_reward(
                death_id, victim, (), (), "invalid_injury", source_id=_object_id(source)
            ),
            (),
        )

    ledger = _ledger(victim)
    history = tuple(_contribution_from_payload(raw) for raw in ledger["contributions"])
    direct = _direct_candidate(source, victim, history)
    candidates = tuple(
        candidate for candidate in (direct, *reversed(history)) if candidate
    )
    rejected: list[tuple[int | None, str]] = []
    recipient = None
    for candidate in candidates:
        if candidate.responsible_id is None:
            rejected.append((None, "no_responsible_pc"))
            continue
        recipient, reason = _eligible_candidate(candidate, victim)
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
            (),
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
            (),
        )
    npc_level = victim.stats.level
    recipients = _eligible_roster(candidate, recipient, victim)
    raw_shares = _split_base_xp(base, len(recipients))
    payout: list[tuple[Any, RewardShare]] = []
    for member, raw_xp in zip(recipients, raw_shares, strict=True):
        multiplier, final_xp = calculate_npc_xp(raw_xp, npc_level, member.stats.level)
        payout.append(
            (
                member,
                RewardShare(
                    member.id,
                    raw_xp,
                    member.stats.level,
                    multiplier,
                    final_xp,
                    member.stats.xp,
                    member.stats.xp + final_xp,
                ),
            )
        )
    first = payout[0][1]
    return (
        RewardResult(
            death_id,
            victim_id,
            victim.key,
            _object_id(source),
            candidates,
            tuple(rejected),
            first.recipient_id,
            base,
            npc_level,
            first.recipient_level,
            first.multiplier,
            sum(share.final_xp for _, share in payout),
            first.previous_xp,
            first.resulting_xp,
            "awarded",
            group_id=candidate.group_id,
            membership_sequence=candidate.membership_sequence,
            frozen_roster=candidate.roster_ids,
            shares=tuple(share for _, share in payout),
        ),
        tuple(payout),
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


def _eligible_candidate(candidate: Contribution, victim: Any) -> tuple[Any | None, str]:
    """Require the credited contributor to remain in its frozen party at death."""
    if candidate.responsible_id is None:
        return None, "no_responsible_pc"
    character, reason = _eligible_recipient(candidate.responsible_id, victim)
    if (
        character is not None
        and candidate.group_id is not None
        and not _still_in_frozen_group(character, candidate.group_id)
    ):
        return None, "former_group_member"
    return character, reason


def _contribution(
    source_id: int | None,
    source_kind: str,
    responsible_id: int | None,
    order: int,
    encounter_id: int | None,
) -> Contribution:
    """Create one ledger candidate with its contributor's frozen party roster."""
    snapshot = _group_snapshot(responsible_id)
    return Contribution(
        source_id,
        source_kind,
        responsible_id,
        order,
        encounter_id,
        snapshot["group_id"] if snapshot else None,
        snapshot["membership_sequence"] if snapshot else None,
        snapshot["member_ids"] if snapshot else (),
    )


def _direct_candidate(
    source: Any | None, victim: Any, history: tuple[Contribution, ...]
) -> Contribution | None:
    """Prefer terminal damage's recorded snapshot for direct attribution."""
    if source is None:
        return None
    source_id, responsible_id = _object_id(source), _responsible_pc_id(source)
    for contribution in reversed(history):
        if (
            contribution.source_id == source_id
            and contribution.responsible_id == responsible_id
        ):
            return contribution
    return _contribution(source_id, "direct", responsible_id, 1, _encounter_id(victim))


def _group_snapshot(character_id: int | None) -> dict[str, Any] | None:
    """Read GROUP-02's immutable-at-contribution roster without coupling storage."""
    if not _positive_int(character_id):
        return None
    character = _character(character_id)
    if character is None:
        return None
    try:
        from systems.groups import reward_roster

        return reward_roster(character)
    except Exception:
        return None


def _eligible_roster(
    candidate: Contribution, credited: Any, victim: Any
) -> tuple[Any, ...]:
    """Filter a frozen roster by current membership and exact death-time state."""
    if candidate.group_id is None or not candidate.roster_ids:
        return (credited,)
    eligible: list[Any] = []
    for member_id in candidate.roster_ids:
        member, _ = _eligible_recipient(member_id, victim)
        if member is not None and _still_in_frozen_group(member, candidate.group_id):
            eligible.append(member)
    # The candidate was independently eligible before this point.  A malformed
    # or concurrently repaired registry must fail closed to its solo award,
    # never silently hand XP to an unrelated current party.
    return tuple(eligible) or (credited,)


def _still_in_frozen_group(character: Any, group_id: int) -> bool:
    """Require an eligible roster member to remain in its credited group."""
    try:
        from systems.groups import group_for

        group = group_for(character)
        return group is not None and group["id"] == group_id
    except Exception:
        return False


def _split_base_xp(base_xp: int, recipient_count: int) -> tuple[int, ...]:
    """Split authored XP in frozen join order, assigning remainder from first."""
    if recipient_count < 1:
        raise RewardError("A reward split requires an eligible recipient.")
    quotient, remainder = divmod(base_xp, recipient_count)
    return tuple(quotient + (index < remainder) for index in range(recipient_count))


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
    contributions = [
        _contribution_payload(_contribution_from_payload(item))
        for item in raw["contributions"]
    ]
    return {
        "version": LEDGER_VERSION,
        "open": raw["open"],
        "encounter_id": raw["encounter_id"],
        "next_order": raw["next_order"],
        "contributions": contributions,
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
        or raw["version"] not in {1, LEDGER_VERSION}
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
        "group_id": item.group_id,
        "membership_sequence": item.membership_sequence,
        "roster_ids": list(item.roster_ids),
    }


def _contribution_from_payload(raw: Any) -> Contribution:
    if isinstance(raw, Mapping) and set(raw) == {
        "source_id",
        "source_kind",
        "responsible_id",
        "order",
        "encounter_id",
    }:
        raw = {**raw, "group_id": None, "membership_sequence": None, "roster_ids": []}
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {
            "source_id",
            "source_kind",
            "responsible_id",
            "order",
            "encounter_id",
            "group_id",
            "membership_sequence",
            "roster_ids",
        }
        or (raw["source_id"] is not None and not _positive_int(raw["source_id"]))
        or not isinstance(raw["source_kind"], str)
        or not raw["source_kind"]
        or (
            raw["responsible_id"] is not None
            and not _positive_int(raw["responsible_id"])
        )
        or not _positive_int(raw["order"])
        or (raw["encounter_id"] is not None and not _positive_int(raw["encounter_id"]))
        or (raw["group_id"] is not None and not _positive_int(raw["group_id"]))
        or (
            raw["membership_sequence"] is not None
            and not isinstance(raw["membership_sequence"], int)
        )
        or not isinstance(raw["roster_ids"], Sequence)
        or isinstance(raw["roster_ids"], (str, bytes))
        or len(raw["roster_ids"]) > 8
        or len(set(raw["roster_ids"])) != len(raw["roster_ids"])
        or not all(_positive_int(item) for item in raw["roster_ids"])
        or ((raw["group_id"] is None) != (raw["membership_sequence"] is None))
        or ((raw["group_id"] is None) != (not bool(raw["roster_ids"])))
    ):
        raise RewardError("Contribution entry is invalid.")
    return Contribution(
        raw["source_id"],
        raw["source_kind"],
        raw["responsible_id"],
        raw["order"],
        raw["encounter_id"],
        raw["group_id"],
        raw["membership_sequence"],
        tuple(raw["roster_ids"]),
    )


def _result_store() -> dict[str, Any]:
    raw = ServerConfig.objects.conf(REWARDS_CONFIG_KEY)
    if raw is None:
        return {"version": REWARDS_VERSION, "results": {}}
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "results"}
        or raw["version"] not in {1, REWARDS_VERSION}
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
        "group_id": result.group_id,
        "membership_sequence": result.membership_sequence,
        "frozen_roster": list(result.frozen_roster),
        "shares": [
            {
                "recipient_id": item.recipient_id,
                "raw_xp": item.raw_xp,
                "recipient_level": item.recipient_level,
                "multiplier": item.multiplier,
                "final_xp": item.final_xp,
                "previous_xp": item.previous_xp,
                "resulting_xp": item.resulting_xp,
            }
            for item in result.shares
        ],
    }


def _result_from_payload(raw: Any) -> RewardResult:
    if not isinstance(raw, Mapping):
        raise RewardError("Reward result is invalid.")
    candidates = tuple(_contribution_from_payload(item) for item in raw["candidates"])
    rejected = tuple((item[0], item[1]) for item in raw["rejected"])
    shares = tuple(_share_from_payload(item) for item in raw.get("shares", ()))
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
        group_id=raw.get("group_id"),
        membership_sequence=raw.get("membership_sequence"),
        frozen_roster=tuple(raw.get("frozen_roster", ())),
        shares=shares,
    )


def _share_from_payload(raw: Any) -> RewardShare:
    """Validate one persisted member payout."""
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {
            "recipient_id",
            "raw_xp",
            "recipient_level",
            "multiplier",
            "final_xp",
            "previous_xp",
            "resulting_xp",
        }
        or not _positive_int(raw["recipient_id"])
        or not isinstance(raw["raw_xp"], int)
        or raw["raw_xp"] < 0
        or not _positive_int(raw["recipient_level"])
        or not isinstance(raw["multiplier"], (int, float))
        or not isinstance(raw["final_xp"], int)
        or raw["final_xp"] < 0
    ):
        raise RewardError("Reward share is invalid.")
    return RewardShare(**raw)


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

"""Canonical ability, skill, tool, passive, and opposed checks for ADV-04.

This module deliberately calculates a check from primitive inputs instead of
calling ``CharacterStats.skill_bonus``.  That keeps every contribution visible
in the immutable result and prevents a caller from accidentally adding
proficiency or an effect modifier for a second time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from numbers import Real
from typing import Any

from evennia.utils import logger
from systems.dice import RollResult, roll, roll_check
from world.chargen_data import ABILITY_NAMES, ABILITY_SHORT, SKILLS

MIN_CHECK_DC = 5
MAX_CHECK_DC = 30
MAX_SAVING_THROW_DC = 100000

_ABILITIES = {
    **{name.casefold(): name for name in ABILITY_NAMES},
    **{short.casefold(): name for name, short in ABILITY_SHORT.items()},
}
_SKILLS = {name.casefold(): name for name in SKILLS}


class CheckError(ValueError):
    """Raised when code attempts to make an invalid check request."""


class RollMode(str, Enum):
    """The d20 selection method represented in a check result."""

    STRAIGHT = "straight"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"
    CANCELLED = "cancelled"
    PASSIVE = "passive"


@dataclass(frozen=True)
class CheckRequest:
    """A side-effect-free request for one fixed-DC ability check."""

    actor: Any
    ability: str
    dc: int
    skill: str | None = None
    tool: str | None = None
    alternate_ability: bool = False
    expertise_multiplier: int = 1
    action_key: str = "general"
    advantage_sources: tuple[str, ...] = ()
    disadvantage_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """Auditable public mechanics for one fixed or passive check."""

    action_key: str
    ability: str
    skill: str | None
    tool: str | None
    roll_mode: RollMode
    die_result: int | None
    ability_modifier: int
    proficiency_contribution: int
    other_modifiers: int
    total: int
    target_dc: int | None
    opposing_total: int | None
    success: bool | None
    tie: bool = False
    reason: str = ""
    advantage_sources: tuple[str, ...] = ()
    disadvantage_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpposedCheckResult:
    """The snapshotted result of two checks, with a stable tie policy."""

    actor: CheckResult
    opponent: CheckResult
    actor_wins: bool
    tie: bool
    tie_policy: str


def validate_dc(value: Any) -> int:
    """Validate a builder or code-owned DC in the documented normal range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckError("A check DC must be a whole number.")
    if not MIN_CHECK_DC <= value <= MAX_CHECK_DC:
        raise CheckError(
            f"A check DC must be between {MIN_CHECK_DC} and {MAX_CHECK_DC}."
        )
    return value


def canonical_ability(value: Any) -> str:
    """Return one canonical ability name, accepting only known aliases."""
    canonical = _ABILITIES.get(str(value).strip().casefold())
    if canonical is None:
        raise CheckError("Unknown check ability.")
    return canonical


def canonical_skill(value: Any) -> str:
    """Return one canonical skill name, rejecting unknown skill text."""
    canonical = _SKILLS.get(str(value).strip().casefold())
    if canonical is None:
        raise CheckError("Unknown check skill.")
    return canonical


def _source_names(sources: Iterable[str], label: str) -> tuple[str, ...]:
    """Return bounded, de-duplicated source names safe for audit output."""
    if isinstance(sources, (str, bytes)):
        raise CheckError(f"{label} sources must be a collection.")
    normalized: set[str] = set()
    for source in sources:
        if not isinstance(source, str):
            raise CheckError(f"{label} sources must be text.")
        name = source.strip().casefold()
        if (
            not name
            or len(name) > 48
            or not all(c.isalnum() or c in "_.-" for c in name)
        ):
            raise CheckError(f"Invalid {label} source.")
        normalized.add(name)
    return tuple(sorted(normalized))


def _proficiency_names(actor: Any, attribute: str) -> set[str]:
    """Read legacy proficiency lists defensively and log malformed records."""
    values = getattr(actor, "attributes", None)
    raw = values.get(attribute) if values is not None else None
    raw = [] if raw is None else raw
    if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, Iterable):
        logger.log_warn(
            f"Ignoring malformed {attribute} on #{getattr(actor, 'id', '?')}."
        )
        return set()
    if any(not isinstance(value, str) for value in raw):
        logger.log_warn(
            f"Ignoring malformed entries in {attribute} on #{getattr(actor, 'id', '?')}."
        )
    return {value.strip().casefold() for value in raw if isinstance(value, str)}


def _modifier_total(actor: Any, *names: str) -> int:
    """Read only named numeric RULES modifier sources exactly once."""
    source_method = getattr(actor, "get_stat_modifier_sources", None)
    if not callable(source_method):
        return 0
    total = 0
    for source in source_method():
        if not isinstance(source, Mapping):
            continue
        for name in names:
            value = source.get(name, 0)
            if isinstance(value, Real) and not isinstance(value, bool):
                total += int(value)
    return total


def _check_parts(
    request: CheckRequest,
) -> tuple[
    str,
    str | None,
    str | None,
    int,
    int,
    int,
    tuple[str, ...],
    tuple[str, ...],
]:
    """Validate a request and calculate all non-random check contributions."""
    if not hasattr(request.actor, "stats"):
        raise CheckError("Checks require a character actor.")
    ability = canonical_ability(request.ability)
    skill = canonical_skill(request.skill) if request.skill is not None else None
    if skill is not None and SKILLS[skill] != ability and not request.alternate_ability:
        raise CheckError("That skill normally uses a different ability.")
    tool = (
        request.tool.strip()
        if isinstance(request.tool, str) and request.tool.strip()
        else None
    )
    if request.tool is not None and tool is None:
        raise CheckError("A tool proficiency must be named.")
    if request.expertise_multiplier not in (1, 2):
        raise CheckError("Expertise must use a multiplier of one or two.")
    if (
        not isinstance(request.action_key, str)
        or not request.action_key
        or len(request.action_key) > 48
    ):
        raise CheckError("A check needs a bounded action key.")
    validate_dc(request.dc)
    ability_modifier = request.actor.stats.ability_modifier(ability)
    skill_proficient = skill is not None and skill.casefold() in _proficiency_names(
        request.actor, "skill_proficiencies"
    )
    tool_proficient = tool is not None and tool.casefold() in _proficiency_names(
        request.actor, "tool_proficiencies"
    )
    proficient = skill_proficient or tool_proficient
    expertise_multiplier = request.expertise_multiplier
    if skill is not None and skill.casefold() in _proficiency_names(
        request.actor, "skill_expertise"
    ):
        expertise_multiplier = 2
    proficiency = (
        request.actor.stats.proficiency_bonus * expertise_multiplier
        if proficient
        else 0
    )
    modifiers = _modifier_total(
        request.actor,
        "check_bonus",
        f"check:{request.action_key.casefold()}",
        f"check:ability:{ability.casefold()}",
        *(("skill_bonus", f"skill:{skill.casefold()}") if skill else ()),
        *((f"tool:{tool.casefold()}",) if tool else ()),
    )
    disadvantage = _source_names(request.disadvantage_sources, "Disadvantage")
    if ability in {"Strength", "Dexterity"} and request.actor.stats.has_untrained_armor:
        disadvantage = tuple(sorted({*disadvantage, "untrained_armor"}))
    advantage = _source_names(request.advantage_sources, "Advantage")
    return (
        ability,
        skill,
        tool,
        ability_modifier,
        proficiency,
        modifiers,
        advantage,
        disadvantage,
    )


def _mode(advantage: tuple[str, ...], disadvantage: tuple[str, ...]) -> RollMode:
    """Collapse any number of sources using the dice service's SRD semantics."""
    if advantage and disadvantage:
        return RollMode.CANCELLED
    if advantage:
        return RollMode.ADVANTAGE
    if disadvantage:
        return RollMode.DISADVANTAGE
    return RollMode.STRAIGHT


def resolve_check(
    request: CheckRequest, *, roller: Callable[[int], int] = roll
) -> CheckResult:
    """Resolve exactly one valid fixed-DC check through ``systems.dice``."""
    (
        ability,
        skill,
        tool,
        ability_mod,
        proficiency,
        modifiers,
        advantage,
        disadvantage,
    ) = _check_parts(request)
    roll_result: RollResult = roll_check(
        ability_mod + proficiency + modifiers,
        request.dc,
        has_advantage=bool(advantage),
        has_disadvantage=bool(disadvantage),
        roller=roller,
    )
    return CheckResult(
        request.action_key,
        ability,
        skill,
        tool,
        _mode(advantage, disadvantage),
        roll_result.die_roll,
        ability_mod,
        proficiency,
        modifiers,
        roll_result.total,
        request.dc,
        None,
        roll_result.success,
        False,
        "",
        advantage,
        disadvantage,
    )


def resolve_saving_throw(
    actor: Any,
    ability: str,
    dc: int,
    *,
    action_key: str,
    roller: Callable[[int], int] = roll,
) -> CheckResult:
    """Resolve one canonical saving throw with its distinct class proficiency.

    Saving throws share dice, ability, and named check modifiers with ADV-04
    checks, but derive their proficiency contribution from the character's
    saving-throw table.  Their DC range permits high-damage concentration
    checks without relaxing ordinary player-facing check validation.
    """
    if not hasattr(actor, "stats"):
        raise CheckError("Saving throws require a character actor.")
    canonical = canonical_ability(ability)
    if (
        isinstance(dc, bool)
        or not isinstance(dc, int)
        or not 1 <= dc <= MAX_SAVING_THROW_DC
    ):
        raise CheckError("A saving throw DC is outside the supported range.")
    if not isinstance(action_key, str) or not action_key or len(action_key) > 48:
        raise CheckError("A saving throw needs a bounded action key.")
    ability_modifier = actor.stats.ability_modifier(canonical)
    saving_throw_bonus = actor.stats.saving_throw_bonus(canonical)
    proficiency = saving_throw_bonus - ability_modifier
    modifiers = _modifier_total(
        actor,
        "check_bonus",
        f"check:{action_key.casefold()}",
        f"check:ability:{canonical.casefold()}",
    )
    roll_result = roll_check(saving_throw_bonus + modifiers, dc, roller=roller)
    return CheckResult(
        action_key,
        canonical,
        None,
        None,
        RollMode.STRAIGHT,
        roll_result.die_roll,
        ability_modifier,
        proficiency,
        modifiers,
        roll_result.total,
        dc,
        None,
        roll_result.success,
    )


def passive_check(
    actor: Any,
    *,
    ability: str,
    skill: str | None = None,
    tool: str | None = None,
    alternate_ability: bool = False,
    expertise_multiplier: int = 1,
    action_key: str = "passive",
    advantage_sources: Iterable[str] = (),
    disadvantage_sources: Iterable[str] = (),
    override: int | None = None,
) -> CheckResult:
    """Calculate a passive score using the same bonuses as an active check."""
    request = CheckRequest(
        actor,
        ability,
        MIN_CHECK_DC,
        skill,
        tool,
        alternate_ability,
        expertise_multiplier,
        action_key,
        tuple(advantage_sources),
        tuple(disadvantage_sources),
    )
    (
        ability,
        skill,
        tool,
        ability_mod,
        proficiency,
        modifiers,
        advantage,
        disadvantage,
    ) = _check_parts(request)
    if override is not None and (
        isinstance(override, bool) or not isinstance(override, int) or override < 0
    ):
        raise CheckError(
            "A passive-score override must be a non-negative whole number."
        )
    # ``passive_perception`` predates ADV-04 and remains a supported named
    # RULES modifier, so existing effects retain their exact meaning.
    modifiers += _modifier_total(actor, "passive_perception")
    base = override if override is not None else 10 + ability_mod + proficiency
    adjustment = (
        5
        if advantage and not disadvantage
        else -5 if disadvantage and not advantage else 0
    )
    total = base + modifiers + adjustment
    return CheckResult(
        action_key,
        ability,
        skill,
        tool,
        RollMode.PASSIVE,
        None,
        ability_mod,
        proficiency,
        modifiers + adjustment,
        total,
        None,
        None,
        None,
        False,
        "",
        advantage,
        disadvantage,
    )


def resolve_opposed_check(
    actor: CheckRequest,
    opponent: CheckRequest,
    *,
    tie_policy: str = "defender_wins",
    roller: Callable[[int], int] = roll,
) -> OpposedCheckResult:
    """Roll each participant once; ties retain the status quo by default."""
    if tie_policy not in {"defender_wins", "attacker_wins"}:
        raise CheckError("Unsupported opposed-check tie policy.")
    first = resolve_check(actor, roller=roller)
    second = resolve_check(opponent, roller=roller)
    tie = first.total == second.total
    actor_wins = first.total > second.total or (tie and tie_policy == "attacker_wins")
    first = replace(
        first,
        target_dc=None,
        opposing_total=second.total,
        success=actor_wins,
        tie=tie,
    )
    second = replace(
        second,
        target_dc=None,
        opposing_total=first.total,
        success=not actor_wins,
        tie=tie,
    )
    return OpposedCheckResult(first, second, actor_wins, tie, tie_policy)


def stealth_against_passive(
    stealth_actor: Any, observer: Any, *, roller: Callable[[int], int] = roll
) -> OpposedCheckResult:
    """Resolve canonical Stealth against an observer's passive Perception."""
    stealth = resolve_check(
        CheckRequest(
            stealth_actor,
            "Dexterity",
            MIN_CHECK_DC,
            skill="Stealth",
            action_key="stealth",
        ),
        roller=roller,
    )
    perception = passive_check(
        observer,
        ability="Wisdom",
        skill="Perception",
        action_key="passive_perception",
        override=(
            getattr(observer, "attributes", None).get("passive_perception_override")
            if getattr(observer, "attributes", None)
            else None
        ),
    )
    actor_wins = stealth.total > perception.total
    stealth = replace(
        stealth,
        target_dc=None,
        opposing_total=perception.total,
        success=actor_wins,
        tie=stealth.total == perception.total,
    )
    perception = replace(
        perception,
        target_dc=None,
        opposing_total=stealth.total,
        success=not actor_wins,
        tie=stealth.tie,
    )
    return OpposedCheckResult(
        stealth, perception, actor_wins, stealth.tie, "defender_wins"
    )


def active_detection(
    observer: Any, hidden_actor: Any, *, roller: Callable[[int], int] = roll
) -> OpposedCheckResult:
    """Resolve active Perception against Stealth without changing either actor."""
    return resolve_opposed_check(
        CheckRequest(
            observer,
            "Wisdom",
            MIN_CHECK_DC,
            skill="Perception",
            action_key="active_detection",
        ),
        CheckRequest(
            hidden_actor,
            "Dexterity",
            MIN_CHECK_DC,
            skill="Stealth",
            action_key="stealth",
        ),
        roller=roller,
    )

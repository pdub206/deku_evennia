"""Deterministic, offline COMBAT-10 balance harness.

Run from the initialized game directory with ``python -m systems.balance``.
It uses the live combat calculation functions but never creates Evennia
objects, loads the encounter registry, sends messages, or writes the database.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from typing import Any

from systems.character_stats import AttackProfile, combat_delay_from_reaction
from systems.combat_math import (
    HIT_LOCATION_WEIGHTS,
    AttackClassification,
    resolve_attack_calculation,
)
from systems.combat_outcomes import (
    InjuryState,
    calculate_npc_xp,
    predict_damage_transition,
)
from systems.equipment import (
    DAMAGE_TYPES,
    HIT_LOCATIONS,
    MAX_MITIGATION_PERCENT,
    DamageMitigation,
)


class BalanceValidationError(ValueError):
    """Raised before an invalid offline scenario can consume a simulation."""


@dataclass(frozen=True)
class MitigationRule:
    """Immutable armor protection expressible without an equipment object."""

    percentage: int = 0
    flat: int = 0
    damage_types: tuple[str, ...] = DAMAGE_TYPES
    locations: tuple[str, ...] = HIT_LOCATIONS

    def __post_init__(self) -> None:
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (self.percentage, self.flat)
            )
            or not self.damage_types
            or not self.locations
            or any(value not in DAMAGE_TYPES for value in self.damage_types)
            or any(value not in HIT_LOCATIONS for value in self.locations)
        ):
            raise BalanceValidationError("Mitigation rule is invalid.")


@dataclass(frozen=True)
class CombatantScenario:
    """All immutable, primitive inputs needed to simulate one combatant.

    ``tactical_action`` models one first-ready-action intent using the same
    attack calculation seam as live combat. ``wimpy_percent`` assumes that an
    eligible flee route exists; the harness deliberately does not model rooms
    or exits, only the combat result of the queued next-ready-action escape.
    """

    name: str
    level: int
    hp: int
    armor_class: int
    profile: AttackProfile
    reaction_modifier: int = 0
    mitigation: tuple[MitigationRule, ...] = ()
    is_npc: bool = False
    xp_reward: int = 0
    uses_death_saves: bool = False
    untrained_armor: bool = False
    tactical_action: str = "basic"
    wimpy_percent: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or isinstance(self.level, bool)
            or not isinstance(self.level, int)
            or not 1 <= self.level <= 20
            or isinstance(self.hp, bool)
            or not isinstance(self.hp, int)
            or self.hp < 1
            or isinstance(self.armor_class, bool)
            or not isinstance(self.armor_class, int)
            or not 0 <= self.armor_class <= 100
            or isinstance(self.reaction_modifier, bool)
            or not isinstance(self.reaction_modifier, int)
            or not -100 <= self.reaction_modifier <= 100
            or isinstance(self.xp_reward, bool)
            or not isinstance(self.xp_reward, int)
            or not 0 <= self.xp_reward <= 1_000_000
            or not isinstance(self.profile, AttackProfile)
            or not isinstance(self.mitigation, tuple)
            or not all(isinstance(rule, MitigationRule) for rule in self.mitigation)
            or self.tactical_action not in {"basic", "kick"}
            or isinstance(self.wimpy_percent, bool)
            or not isinstance(self.wimpy_percent, int)
            or not 0 <= self.wimpy_percent <= 90
        ):
            raise BalanceValidationError("Combatant scenario is invalid.")


@dataclass(frozen=True)
class BalanceScenario:
    """A bounded two-side matchup identified independently of its display name."""

    identity: str
    teams: tuple[tuple[CombatantScenario, ...], tuple[CombatantScenario, ...]]
    max_actions: int = 10_000
    max_rounds: int = 1_000
    base_delay: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity, str)
            or not self.identity.strip()
            or not isinstance(self.teams, tuple)
            or len(self.teams) != 2
            or any(not isinstance(team, tuple) or not team for team in self.teams)
            or not all(
                isinstance(member, CombatantScenario)
                for team in self.teams
                for member in team
            )
            or isinstance(self.max_actions, bool)
            or not isinstance(self.max_actions, int)
            or self.max_actions < 1
            or isinstance(self.max_rounds, bool)
            or not isinstance(self.max_rounds, int)
            or self.max_rounds < 1
            or isinstance(self.base_delay, bool)
            or not isinstance(self.base_delay, (int, float))
            or self.base_delay <= 0
        ):
            raise BalanceValidationError("Balance scenario is invalid.")


@dataclass(frozen=True)
class BalanceResult:
    """Stable aggregate measurements for one scenario and one explicit seed."""

    scenario: str
    seed: int
    iterations: int
    wins: tuple[int, int]
    losses: tuple[int, int]
    draws: int
    stalls: int
    fled: tuple[int, int]
    win_rates: tuple[float, float]
    loss_rates: tuple[float, float]
    draw_rate: float
    stall_rate: float
    flee_rates: tuple[float, float]
    hit_rate: float
    critical_rate: float
    damage_per_action: float
    damage_per_round: float
    mean_actions_to_defeat: float | None
    mean_rounds_to_defeat: float | None
    time_to_kill_p50: float | None
    time_to_kill_p90: float | None
    mean_survivor_hp: tuple[float, float]
    expected_xp: float
    xp_per_combat_time: float
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return JSON/CSV-safe primitives with fixed, documented field names."""
        return asdict(self)


@dataclass
class _CombatantState:
    """Mutable per-run state; never exposed or persisted outside the harness."""

    scenario: CombatantScenario
    team: int
    order: int
    hp: int
    injury: InjuryState = InjuryState.CONSCIOUS
    next_action: float = 0.0
    opening_tactical_consumed: bool = False
    pending_flee: bool = False

    @property
    def alive(self) -> bool:
        """A zero-HP injury is combat-terminal even when live PCs later save."""
        return self.hp > 0 and self.injury is InjuryState.CONSCIOUS


def run_scenario(
    scenario: BalanceScenario, *, iterations: int, seed: int
) -> BalanceResult:
    """Run a validated scenario repeatedly from a local ``Random(seed)`` stream."""
    _validate_run_arguments(iterations, seed)
    rng = random.Random(seed)
    wins = [0, 0]
    fled = [0, 0]
    draws = stalls = hits = criticals = actions = rounds = total_damage = 0
    defeat_actions: list[int] = []
    defeat_rounds: list[int] = []
    survivor_hp = [0, 0]
    xp_total = 0
    diagnostics: set[str] = set()
    for _ in range(iterations):
        outcome = _run_once(scenario, rng)
        actions += outcome["actions"]
        rounds += outcome["rounds"]
        hits += outcome["hits"]
        criticals += outcome["criticals"]
        total_damage += outcome["damage"]
        xp_total += outcome["xp"]
        survivor_hp[0] += outcome["survivor_hp"][0]
        survivor_hp[1] += outcome["survivor_hp"][1]
        diagnostics.add(outcome["reason"])
        if outcome["reason"] == "mutual_defeat":
            draws += 1
        elif outcome["winner"] is None:
            stalls += 1
        else:
            wins[outcome["winner"]] += 1
            if outcome["fled_team"] is not None:
                fled[outcome["fled_team"]] += 1
            defeat_actions.append(outcome["actions"])
            defeat_rounds.append(outcome["rounds"])
    completed_time = sum(defeat_rounds)
    return BalanceResult(
        scenario=scenario.identity,
        seed=seed,
        iterations=iterations,
        wins=tuple(wins),
        losses=(wins[1], wins[0]),
        draws=draws,
        stalls=stalls,
        fled=tuple(fled),
        win_rates=(wins[0] / iterations, wins[1] / iterations),
        loss_rates=(wins[1] / iterations, wins[0] / iterations),
        draw_rate=draws / iterations,
        stall_rate=stalls / iterations,
        flee_rates=(fled[0] / iterations, fled[1] / iterations),
        hit_rate=hits / actions if actions else 0.0,
        critical_rate=criticals / actions if actions else 0.0,
        damage_per_action=total_damage / actions if actions else 0.0,
        damage_per_round=total_damage / rounds if rounds else 0.0,
        mean_actions_to_defeat=_mean(defeat_actions),
        mean_rounds_to_defeat=_mean(defeat_rounds),
        time_to_kill_p50=_percentile(defeat_rounds, 0.5),
        time_to_kill_p90=_percentile(defeat_rounds, 0.9),
        mean_survivor_hp=(survivor_hp[0] / iterations, survivor_hp[1] / iterations),
        expected_xp=xp_total / iterations,
        xp_per_combat_time=xp_total / completed_time if completed_time else 0.0,
        diagnostics=tuple(sorted(diagnostics)),
    )


def run_matrix(*, iterations: int, seed: int) -> tuple[BalanceResult, ...]:
    """Run the versioned standard COMBAT-10 comparison matrix."""
    return tuple(
        run_scenario(scenario, iterations=iterations, seed=seed + index)
        for index, scenario in enumerate(standard_matrix())
    )


def standard_matrix() -> tuple[BalanceScenario, ...]:
    """Return representative gear, level, cadence, mitigation, and team fights."""
    unarmed = AttackProfile("unarmed", "Strength", 4, None, 1, 2, "bludgeoning", True)
    sword = AttackProfile(
        "one-handed sword", "Strength", 5, "1d8", 0, 2, "slashing", True
    )
    greatsword = AttackProfile(
        "two-handed sword", "Strength", 5, "2d6", 0, 2, "slashing", True
    )
    light = (
        MitigationRule(10, 0, ("slashing", "piercing", "bludgeoning"), HIT_LOCATIONS),
    )
    medium = (
        MitigationRule(20, 1, ("slashing", "piercing", "bludgeoning"), HIT_LOCATIONS),
    )
    heavy = (
        MitigationRule(35, 2, ("slashing", "piercing", "bludgeoning"), HIT_LOCATIONS),
    )
    fire_body = (MitigationRule(40, 2, ("fire",), ("body",)),)

    def fighter(
        name: str,
        level: int = 3,
        hp: int = 28,
        ac: int = 14,
        profile: AttackProfile = sword,
        **kwargs: Any,
    ) -> CombatantScenario:
        return CombatantScenario(name, level, hp, ac, profile, **kwargs)

    def versus(
        identity: str, left: CombatantScenario, right: CombatantScenario
    ) -> BalanceScenario:
        return BalanceScenario(identity, ((left,), (right,)))

    return (
        versus(
            "equal_unarmed",
            fighter("PC", profile=unarmed),
            fighter("NPC", profile=unarmed, is_npc=True, xp_reward=100),
        ),
        versus(
            "unarmed_vs_armed",
            fighter("PC", profile=unarmed),
            fighter("NPC", profile=sword, is_npc=True, xp_reward=100),
        ),
        versus(
            "light_armor",
            fighter("PC", ac=12, mitigation=light),
            fighter("NPC", ac=12, mitigation=light, is_npc=True, xp_reward=100),
        ),
        versus(
            "medium_armor",
            fighter("PC", ac=14, mitigation=medium),
            fighter("NPC", ac=14, mitigation=medium, is_npc=True, xp_reward=100),
        ),
        versus(
            "heavy_armor",
            fighter("PC", ac=17, mitigation=heavy),
            fighter("NPC", ac=17, mitigation=heavy, is_npc=True, xp_reward=100),
        ),
        versus(
            "one_handed_vs_heavy_weapon",
            fighter("PC", profile=sword),
            fighter("NPC", profile=greatsword, is_npc=True, xp_reward=100),
        ),
        *tuple(
            versus(
                f"level_delta_{delta:+d}",
                fighter(
                    "PC",
                    level=11 if delta < 0 else 10,
                    hp=50,
                    ac=16,
                    profile=greatsword,
                ),
                fighter(
                    "NPC",
                    level=(11 if delta < 0 else 10) + delta,
                    hp=50 + delta * 2,
                    ac=16 + max(0, delta // 3),
                    profile=greatsword,
                    is_npc=True,
                    xp_reward=100,
                ),
            )
            for delta in (-10, -5, -3, -1, 1, 3, 5, 10)
        ),
        versus(
            "reaction_cadence",
            fighter("quick PC", reaction_modifier=10),
            fighter("slow NPC", reaction_modifier=-10, is_npc=True, xp_reward=100),
        ),
        versus(
            "opening_kick",
            fighter("kicking PC", tactical_action="kick"),
            fighter("NPC", is_npc=True, xp_reward=100),
        ),
        versus(
            "automatic_flee",
            fighter("wimpy PC", hp=20, wimpy_percent=50),
            fighter("NPC", profile=greatsword, is_npc=True, xp_reward=100),
        ),
        versus(
            "locational_fire_mitigation",
            fighter(
                "fire PC",
                profile=AttackProfile(
                    "flame", "Strength", 5, "1d8", 0, 2, "fire", True
                ),
            ),
            fighter("protected NPC", mitigation=fire_body, is_npc=True, xp_reward=100),
        ),
        BalanceScenario(
            "two_pcs_vs_npc",
            (
                (fighter("PC one"), fighter("PC two")),
                (
                    fighter(
                        "NPC", hp=50, profile=greatsword, is_npc=True, xp_reward=150
                    ),
                ),
            ),
        ),
    )


def scenario_from_dict(raw: Mapping[str, Any]) -> BalanceScenario:
    """Build one strictly validated scenario from portable JSON primitives."""
    try:
        teams = tuple(
            tuple(_combatant_from_dict(member) for member in team)
            for team in raw["teams"]
        )
        return BalanceScenario(
            str(raw["identity"]),
            teams,
            int(raw.get("max_actions", 10_000)),
            int(raw.get("max_rounds", 1_000)),
            float(raw.get("base_delay", 1.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BalanceValidationError("Scenario JSON is malformed.") from exc


def render_results(results: Sequence[BalanceResult], output_format: str) -> str:
    """Render stable human, JSON, or CSV output without external dependencies."""
    if output_format == "json":
        return json.dumps(
            [result.as_dict() for result in results], sort_keys=True, indent=2
        )
    if output_format == "csv":
        rows = [_flatten_result(result) for result in results]
        if not rows:
            return ""
        import io

        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()
    if output_format != "summary":
        raise BalanceValidationError("Output format must be summary, json, or csv.")
    headers = (
        "Scenario",
        "Seed",
        "Runs",
        "Wins A/B",
        "Win% A/B",
        "Draw%",
        "Stall",
        "Stall%",
        "Flee A/B",
        "Hit",
        "Crit",
        "Dmg/act",
        "TTK p50",
        "XP/run",
    )
    rows = tuple(
        (
            result.scenario,
            str(result.seed),
            str(result.iterations),
            f"{result.wins[0]}/{result.wins[1]}",
            f"{result.win_rates[0]:.1%}/{result.win_rates[1]:.1%}",
            f"{result.draw_rate:.1%}",
            str(result.stalls),
            f"{result.stall_rate:.1%}",
            f"{result.fled[0]}/{result.fled[1]}",
            f"{result.hit_rate:.1%}",
            f"{result.critical_rate:.1%}",
            f"{result.damage_per_action:.2f}",
            f"{_display(result.time_to_kill_p50)} r",
            f"{result.expected_xp:.2f}",
        )
        for result in results
    )
    widths = tuple(
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    )

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(
            value.ljust(widths[index]) if index == 0 else value.rjust(widths[index])
            for index, value in enumerate(row)
        )

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join((format_row(headers), divider, *(format_row(row) for row in rows)))


def main(argv: Sequence[str] | None = None) -> int:
    """Provide the offline developer entry point used by CI and balance work."""
    parser = argparse.ArgumentParser(
        description="Run deterministic COMBAT-10 balance scenarios."
    )
    parser.add_argument(
        "--scenario", type=Path, help="JSON scenario file (one object or a list)."
    )
    parser.add_argument("--iterations", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--format", choices=("summary", "json", "csv"), default="summary"
    )
    parser.add_argument(
        "--output", type=Path, help="Optional output file; stdout is the default."
    )
    args = parser.parse_args(argv)
    try:
        if args.scenario:
            raw = json.loads(args.scenario.read_text(encoding="utf-8"))
            entries: Iterable[Mapping[str, Any]] = (
                raw if isinstance(raw, list) else (raw,)
            )
            results = tuple(
                run_scenario(
                    scenario_from_dict(entry),
                    iterations=args.iterations,
                    seed=args.seed + index,
                )
                for index, entry in enumerate(entries)
            )
        else:
            results = run_matrix(iterations=args.iterations, seed=args.seed)
        rendered = render_results(results, args.format)
    except (OSError, json.JSONDecodeError, BalanceValidationError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.write_text(
            rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8"
        )
    else:
        print(rendered)
    return 0


def _run_once(scenario: BalanceScenario, rng: random.Random) -> dict[str, Any]:
    states = [
        _CombatantState(member, team, order, member.hp)
        for team, members in enumerate(scenario.teams)
        for order, member in enumerate(members)
    ]
    actions = hits = criticals = damage = xp = 0
    for round_number in range(1, scenario.max_rounds + 1):
        ready = sorted(
            (
                state
                for state in states
                if state.alive and state.next_action <= round_number
            ),
            key=lambda state: (state.next_action, state.team, state.order),
        )
        while ready:
            if actions >= scenario.max_actions:
                return _outcome(
                    None,
                    "action_limit",
                    actions,
                    round_number,
                    hits,
                    criticals,
                    damage,
                    xp,
                    states,
                )
            actor = ready.pop(0)
            if not actor.alive:
                continue
            if actor.pending_flee:
                actions += 1
                return _outcome(
                    1 - actor.team,
                    "fled",
                    actions,
                    round_number,
                    hits,
                    criticals,
                    damage,
                    xp,
                    states,
                    fled_team=actor.team,
                )
            targets = [
                state for state in states if state.team != actor.team and state.alive
            ]
            if not targets:
                return _outcome(
                    actor.team,
                    "defeat",
                    actions,
                    round_number,
                    hits,
                    criticals,
                    damage,
                    xp,
                    states,
                )
            target = min(targets, key=lambda state: (state.hp, state.order))
            profile, delay_multiplier = _next_attack(actor)
            calculation = resolve_attack_calculation(
                profile,
                target.scenario.armor_class,
                target_unconscious=False,
                attacker_untrained_armor=actor.scenario.untrained_armor,
                has_advantage=False,
                has_disadvantage=False,
                extra_damage_dice=None,
                roller=lambda sides: rng.randint(1, sides),
                select_location=lambda: _select_location(rng),
                mitigate=lambda amount, location, damage_type: _mitigate(
                    target.scenario, amount, location, damage_type
                ),
            )
            actions += 1
            actor.next_action += (
                combat_delay_from_reaction(
                    actor.scenario.reaction_modifier, scenario.base_delay
                )
                * delay_multiplier
            )
            if calculation.outcome is not AttackClassification.MISS:
                hits += 1
            if calculation.outcome is AttackClassification.CRITICAL:
                criticals += 1
            damage += calculation.final_damage
            previous_hp = target.hp
            transition = predict_damage_transition(
                previous_hp,
                target.scenario.hp,
                target.injury,
                0,
                0,
                calculation.final_damage,
                critical=calculation.outcome is AttackClassification.CRITICAL,
                uses_death_saves=target.scenario.uses_death_saves,
            )
            target.hp, target.injury = transition.resulting_hp, transition.state
            if _wimpy_crossed(target, previous_hp):
                target.pending_flee = True
            if (
                not target.alive
                and target.scenario.is_npc
                and not actor.scenario.is_npc
            ):
                _, award = calculate_npc_xp(
                    target.scenario.xp_reward,
                    target.scenario.level,
                    actor.scenario.level,
                )
                xp += award
            alive_teams = {state.team for state in states if state.alive}
            if len(alive_teams) == 1:
                return _outcome(
                    alive_teams.pop(),
                    "defeat",
                    actions,
                    round_number,
                    hits,
                    criticals,
                    damage,
                    xp,
                    states,
                )
            if not alive_teams:
                return _outcome(
                    None,
                    "mutual_defeat",
                    actions,
                    round_number,
                    hits,
                    criticals,
                    damage,
                    xp,
                    states,
                )
            ready = sorted(
                (
                    state
                    for state in states
                    if state.alive and state.next_action <= round_number
                ),
                key=lambda state: (state.next_action, state.team, state.order),
            )
    return _outcome(
        None,
        "round_limit",
        actions,
        scenario.max_rounds,
        hits,
        criticals,
        damage,
        xp,
        states,
    )


def _outcome(
    winner: int | None,
    reason: str,
    actions: int,
    rounds: int,
    hits: int,
    criticals: int,
    damage: int,
    xp: int,
    states: Sequence[_CombatantState],
    *,
    fled_team: int | None = None,
) -> dict[str, Any]:
    return {
        "winner": winner,
        "reason": reason,
        "actions": actions,
        "rounds": rounds,
        "hits": hits,
        "criticals": criticals,
        "damage": damage,
        "xp": xp,
        "fled_team": fled_team,
        "survivor_hp": tuple(
            sum(state.hp for state in states if state.team == team and state.alive)
            for team in (0, 1)
        ),
    }


def _mitigate(
    combatant: CombatantScenario, amount: int, location: str, damage_type: str
) -> DamageMitigation:
    applicable = [
        rule
        for rule in combatant.mitigation
        if location in rule.locations and damage_type in rule.damage_types
    ]
    percentage = min(
        MAX_MITIGATION_PERCENT, sum(rule.percentage for rule in applicable)
    )
    flat = sum(rule.flat for rule in applicable)
    final = max(0, amount - amount * percentage // 100 - flat)
    return DamageMitigation(amount, final, percentage, flat)


def _select_location(rng: random.Random) -> str:
    selected = rng.randint(1, sum(HIT_LOCATION_WEIGHTS.values()))
    for location, weight in HIT_LOCATION_WEIGHTS.items():
        selected -= weight
        if selected <= 0:
            return location
    raise RuntimeError("Hit location weights are invalid.")


def _next_attack(state: _CombatantState) -> tuple[AttackProfile, float]:
    """Return this ready action's live-equivalent profile and delay multiplier."""
    if state.opening_tactical_consumed or state.scenario.tactical_action == "basic":
        return state.scenario.profile, 1.0
    state.opening_tactical_consumed = True
    profile = state.scenario.profile
    return (
        AttackProfile(
            "kick",
            profile.ability,
            profile.attack_bonus,
            "1d4",
            0,
            profile.damage_bonus,
            "bludgeoning",
            True,
        ),
        1.5,
    )


def _wimpy_crossed(state: _CombatantState, previous_hp: int) -> bool:
    """Match COMBAT-09's one-way PC threshold trigger in an exitless model."""
    threshold = state.scenario.wimpy_percent
    boundary = state.scenario.hp * threshold / 100
    return bool(
        threshold
        and not state.scenario.is_npc
        and state.injury is InjuryState.CONSCIOUS
        and previous_hp > boundary >= state.hp
    )


def _combatant_from_dict(raw: Mapping[str, Any]) -> CombatantScenario:
    profile_raw = raw["profile"]
    profile = AttackProfile(**profile_raw)
    mitigation = tuple(
        MitigationRule(
            percentage=rule.get("percentage", 0),
            flat=rule.get("flat", 0),
            damage_types=tuple(rule.get("damage_types", DAMAGE_TYPES)),
            locations=tuple(rule.get("locations", HIT_LOCATIONS)),
        )
        for rule in raw.get("mitigation", ())
    )
    return CombatantScenario(
        name=raw["name"],
        level=raw["level"],
        hp=raw["hp"],
        armor_class=raw["armor_class"],
        profile=profile,
        reaction_modifier=raw.get("reaction_modifier", 0),
        mitigation=mitigation,
        is_npc=raw.get("is_npc", False),
        xp_reward=raw.get("xp_reward", 0),
        uses_death_saves=raw.get("uses_death_saves", False),
        untrained_armor=raw.get("untrained_armor", False),
        tactical_action=raw.get("tactical_action", "basic"),
        wimpy_percent=raw.get("wimpy_percent", 0),
    )


def _validate_run_arguments(iterations: int, seed: int) -> None:
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or iterations < 1
    ):
        raise BalanceValidationError("Iterations must be a positive integer.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise BalanceValidationError("Seed must be an integer.")


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: Sequence[int], percentile: float) -> float | None:
    return float(sorted(values)[ceil(len(values) * percentile) - 1]) if values else None


def _flatten_result(result: BalanceResult) -> dict[str, Any]:
    data = result.as_dict()
    data["wins_side_0"], data["wins_side_1"] = data.pop("wins")
    data["survivor_hp_side_0"], data["survivor_hp_side_1"] = data.pop(
        "mean_survivor_hp"
    )
    data["diagnostics"] = ";".join(data["diagnostics"])
    return data


def _display(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


if __name__ == "__main__":
    sys.exit(main())

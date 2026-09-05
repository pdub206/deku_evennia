#!/usr/bin/env bash
# run_sims.sh — run COMBAT-10's deterministic, offline balance simulations.
#
# Usage:
#   ./run_sims.sh
#       Runs the standard combat matrix with 1,000 iterations, seed 1, and a
#       readable table. This is the normal first check after a combat,
#       equipment, progression, or mitigation rules change.
#
#   ./run_sims.sh --iterations 10000 --seed 42 --format json --output balance.json
#       Runs a larger, repeatable sample and writes machine-readable results.
#       The path is relative to the repository root. Use an absolute path if
#       preferred.
#
#   ./run_sims.sh --scenario sim_scenarios/my-matchup.json --format csv
#       Runs one custom JSON scenario (or a JSON list of scenarios). See the
#       immutable input fields in game_files/systems/balance.py.
#
# Expected outcome:
#   The standard matrix prints one table row per matchup. It includes wins for
#   sides A/B, stall count, hit and critical rates, damage per action, median
#   time-to-kill, and expected XP. Re-running with the same rules, seed, and
#   iteration count must produce identical output. The harness never starts the
#   server or writes rooms, encounters, characters, corpses, XP, or database
#   state.
#
# Reading the table:
#   Scenario  - Stable name of the matchup being simulated. Rows whose names
#               begin with `example_` come from a supplied JSON scenario.
#   Seed      - Starting value for this row's private random-number generator.
#               The standard matrix increments the requested seed for each row.
#               Same rules + seed + runs must give exactly the same result.
#   Runs      - Number of independent fights simulated for this matchup.
#   Wins A/B  - Completed fights won by side A / side B. In the standard matrix,
#               A is the first team listed (normally PC(s)); B is the NPC team.
#               Runs not counted as wins reached a safety limit (see Stall).
#   Win% A/B  - Win rates for sides A/B; losses are the opposing side's win rate.
#   Draw%     - Mutual defeats as a rate of all runs.
#   Stall     - Fights that hit their configured action or round limit without a
#               winner. Zero is expected for ordinary working matchups.
#   Hit       - Percentage of all actions that landed, including critical hits.
#   Stall%    - Rate of safety-limit stalls across all runs.
#   Flee A/B  - Completed automatic-flee exits by side; these assume one eligible
#               route because the offline harness does not create rooms or exits.
#   Crit      - Percentage of all actions that landed as critical hits. A normal
#               d20 matchup should trend near 5% as Runs becomes large.
#   Dmg/act   - Mean final HP damage per attempted action, including misses and
#               armor mitigation. Higher normally means a faster fight.
#   TTK p50   - Median completed-fight duration in combat rounds (`r`). A dash
#               means no fight completed, usually because every run stalled.
#   XP/run    - Mean COMBAT-07 NPC XP granted per simulated fight. It is zero
#               when side A does not defeat the XP-bearing NPC.
#
# What the standard-matrix rows test:
#   equal_unarmed              Baseline d20, unarmed damage, and equal stats.
#   unarmed_vs_armed           Weapon dice and damage advantage over unarmed.
#   light_armor                Light AC and physical-damage mitigation.
#   medium_armor               Medium AC and stronger mitigation.
#   heavy_armor                Heavy AC and strongest representative mitigation.
#   one_handed_vs_heavy_weapon Damage-die tradeoff: 1d8 versus 2d6 weapon.
#   level_delta_-10 ... +10    Level, HP, AC, and XP-adjustment differences at
#                              the representative -10/-5/-3/-1/+1/+3/+5/+10 gaps.
#   reaction_cadence           Reaction-derived action-delay advantage (+10 vs -10).
#   opening_kick               One queued COMBAT-08 kick and its 150% follow-up delay.
#   automatic_flee             A COMBAT-09 50% wimpy crossing with an eligible exit.
#   locational_fire_mitigation Fire damage against body-only fire protection.
#   two_pcs_vs_npc             Team targeting, multiple attackers, and NPC XP.
#
# What to investigate when output looks wrong:
#   * A non-zero `Stall` means the action or round safety limit was reached;
#     inspect a zero-damage matchup or explicitly raised limits in that scenario.
#   * A dramatic win-rate, damage, or time-to-kill change with the same seed
#     signals that a combat rule/equipment change affected balance.
#   * A parser error generally means malformed scenario JSON or an invalid
#     iteration count, seed, mitigation rule, or combatant input.
#   * "No module named systems" or a missing virtual environment means run
#     ./setup.sh before trying again.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/game_files"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

if [[ ! -x "$PYTHON" ]]; then
    die "Virtual environment not found. Run ./setup.sh first."
fi
if [[ ! -d "$SOURCE_DIR/systems" ]]; then
    die "game_files/systems/ directory not found. Run this from the repository."
fi

# Defaults make ad-hoc runs reproducible. argparse accepts a later duplicate
# option, so callers can override any of these simply by passing it below.
DEFAULT_ARGS=(--iterations 1000 --seed 1 --format summary)

echo "Running the offline COMBAT-10 balance matrix (no game state is changed)..."
# Point Python at source-of-truth files rather than game/, which makes the
# result reflect unsynced edits too and keeps this developer tool fully offline.
cd "$SCRIPT_DIR"
PYTHONPATH="$SOURCE_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m systems.balance "${DEFAULT_ARGS[@]}" "$@"

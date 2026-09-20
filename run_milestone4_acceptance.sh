#!/usr/bin/env bash
# Run Milestone 4's repeatable vertical acceptance harness.
#
# This harness deliberately uses Evennia's disposable test database. It never
# mutates a running game's world or requires Builder commands. `restore` makes
# the runtime test import path reflect the source-of-truth game_files/ tree.
#
# Roadmap gate mapping:
#   1 doors, discovery, delayed terrain travel, daylight/weather/reload
#   2 containers, recursive capacity, and atomic item movement
#   3 NPC/PC corpse ownership, loot, escrow, and decay
#   4 shops, currency, mundane/magical consumables, retry conservation
#   5 clock boundaries, restock/recharge, and area weather persistence
#   6 presentation preferences, private inspection, notes protocol rendering
#   7 validated starting packages and idempotent chargen grants
#   8 safe diagnostics/repair records for doors, actions, currency, shops,
#     timed items, clock, and weather
#
# Usage: ./run_milestone4_acceptance.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" || ! -d "$ROOT_DIR/game" ]]; then
    echo "ERROR: initialize the project first with ./setup.sh" >&2
    exit 1
fi

"$ROOT_DIR/sync_game_files.sh" restore

cd "$ROOT_DIR/game"
exec "$PYTHON" -m evennia test --settings settings.py \
    commands.tests.test_doors \
    commands.tests.test_generic \
    commands.tests.test_presentation \
    typeclasses.tests.test_action_queue \
    typeclasses.tests.test_consumables \
    typeclasses.tests.test_corpses \
    typeclasses.tests.test_doors \
    typeclasses.tests.test_encumbrance \
    typeclasses.tests.test_item_resources \
    typeclasses.tests.test_magic_items \
    typeclasses.tests.test_notes \
    typeclasses.tests.test_shop_transactions \
    typeclasses.tests.test_shops \
    typeclasses.tests.test_starting_grants \
    typeclasses.tests.test_starting_packages \
    typeclasses.tests.test_travel \
    typeclasses.tests.test_visibility \
    typeclasses.tests.test_weather \
    typeclasses.tests.test_world_clock

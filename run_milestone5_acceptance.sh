#!/usr/bin/env bash
# Run Milestone 5's repeatable groups, communication, and community gate.
#
# The selected modules map to the roadmap's eight acceptance outcomes: follow
# and party capacity; delayed follower travel; assist/rescue/mobile targeting;
# reward rosters and reserved corpses; lifecycle cleanup; speech/tells/channels/
# socials; mail and boards; and player/staff reports. It uses Evennia's isolated
# test database and does not alter a running game world.
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
    commands.tests.test_following \
    commands.tests.test_groups \
    commands.tests.test_communication \
    commands.tests.test_information \
    systems.tests.test_reports \
    typeclasses.tests.test_player_following \
    typeclasses.tests.test_groups \
    typeclasses.tests.test_corpses \
    typeclasses.tests.test_rewards \
    typeclasses.tests.test_tactical_combat \
    typeclasses.tests.test_mob_combat \
    typeclasses.tests.test_communication \
    typeclasses.tests.test_channels \
    typeclasses.tests.test_socials

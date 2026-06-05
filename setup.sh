#!/usr/bin/env bash
# setup.sh
#
# One-time setup for a fresh clone:
#   1. Create a Python virtual environment at .venv/
#   2. Install Python dependencies from requirements.txt
#   3. Initialize the Evennia game directory (game/)
#   4. Restore tracked game files from game_files/ (if present)
#   5. Run evennia migrate to prepare the database
#
# After this, start the game with:
#   cd game && ../.venv/bin/evennia start

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
GAME="$SCRIPT_DIR/game"
FILES="$SCRIPT_DIR/game_files"
EVENNIA="$VENV/bin/evennia"

# ---------------------------------------------------------------------------
# 1. Virtual environment
# ---------------------------------------------------------------------------
if [[ ! -d "$VENV" ]]; then
    echo "Creating virtual environment at .venv/ ..."
    python3 -m venv "$VENV"
else
    echo "Virtual environment already exists at .venv/, skipping creation."
fi

# ---------------------------------------------------------------------------
# 2. Install dependencies
# ---------------------------------------------------------------------------
echo "Installing Python requirements ..."
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 3. Evennia --init
# ---------------------------------------------------------------------------
if [[ -d "$GAME" ]]; then
    echo "game/ already exists, skipping evennia --init."
else
    echo "Initializing Evennia game directory ..."
    (cd "$SCRIPT_DIR" && "$EVENNIA" --init game)
fi

# ---------------------------------------------------------------------------
# 4. Restore game_files/ into game/
# ---------------------------------------------------------------------------
if [[ -d "$FILES" ]]; then
    echo "Restoring tracked game files from game_files/ ..."
    "$SCRIPT_DIR/sync_game_files.sh" restore
else
    echo "No game_files/ directory found, skipping restore."
fi

# ---------------------------------------------------------------------------
# 5. Migrate database
# ---------------------------------------------------------------------------
echo "Running evennia migrate ..."
(cd "$GAME" && "$EVENNIA" migrate)

echo ""
echo "Setup complete."
echo ""
echo "To start the game:"
echo "  cd game && ../.venv/bin/evennia start"

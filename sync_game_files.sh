#!/usr/bin/env bash
# sync_game_files.sh
#
# Usage:
#   ./sync_game_files.sh save     — copy game/ → game_files/ (save your work)
#   ./sync_game_files.sh restore  — copy game_files/ → game/ (after fresh evennia --init)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="$SCRIPT_DIR/game"
FILES="$SCRIPT_DIR/game_files"

RSYNC_OPTS=(-a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' --exclude='*.db3' --exclude='*.pid' --exclude='*.restart' --exclude='*.log' --exclude='*.log.*' --exclude='secret_settings.py')

# Directories to sync (relative to game/)
SYNC_TARGETS=(
    "server/conf"
    "typeclasses"
    "commands"
    "world"
    "web"
    "systems"
)

usage() {
    echo "Usage: $0 [save|restore]"
    echo ""
    echo "  save     Copy game/ → game_files/  (snapshot your work into version control)"
    echo "  restore  Copy game_files/ → game/  (load your work into a fresh game directory)"
    exit 1
}

check_game_dir() {
    if [[ ! -d "$GAME" ]]; then
        echo "ERROR: game/ directory not found at $GAME"
        echo "Run: evennia --init game && evennia migrate"
        exit 1
    fi
}

check_files_dir() {
    if [[ ! -d "$FILES" ]]; then
        echo "ERROR: game_files/ directory not found at $FILES"
        exit 1
    fi
}

do_save() {
    check_game_dir
    check_files_dir
    echo "Saving game/ → game_files/ ..."
    for target in "${SYNC_TARGETS[@]}"; do
        src="$GAME/$target"
        dst="$FILES/$target"
        if [[ -d "$src" ]]; then
            mkdir -p "$dst"
            rsync "${RSYNC_OPTS[@]}" "$src/" "$dst/"
            echo "  synced: $target"
        else
            echo "  skipped (not found): $target"
        fi
    done
    echo "Done. game_files/ is up to date."
}

do_restore() {
    check_game_dir
    check_files_dir
    echo "Restoring game_files/ → game/ ..."
    for target in "${SYNC_TARGETS[@]}"; do
        src="$FILES/$target"
        dst="$GAME/$target"
        if [[ -d "$src" ]]; then
            mkdir -p "$dst"
            rsync "${RSYNC_OPTS[@]}" "$src/" "$dst/"
            echo "  restored: $target"
        else
            echo "  skipped (not in game_files): $target"
        fi
    done
    echo "Done. game/ is loaded with your game files."
    echo ""
    echo "Next steps if this is a fresh install:"
    echo "  cd game && evennia migrate && evennia start"
}

case "${1:-}" in
    save)    do_save ;;
    restore) do_restore ;;
    *)       usage ;;
esac

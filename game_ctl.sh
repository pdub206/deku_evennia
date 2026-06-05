#!/usr/bin/env bash
# game_ctl.sh — control the Deku MUD game server
#
# Usage:
#   ./game_ctl.sh start          — launch server + portal
#   ./game_ctl.sh stop           — graceful shutdown
#   ./game_ctl.sh reload         — hot-reload code (no disconnect)
#   ./game_ctl.sh restart        — full shutdown then start (alias: reboot)
#   ./game_ctl.sh kill           — force-kill server + portal
#   ./game_ctl.sh status         — show running state
#   ./game_ctl.sh logs           — tail server + portal logs

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="$SCRIPT_DIR/game"
EVENNIA="$SCRIPT_DIR/.venv/bin/evennia"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
    echo "ERROR: $*" >&2
    exit 1
}

check_setup() {
    if [[ ! -f "$EVENNIA" ]]; then
        die "Virtual environment not found. Run ./setup.sh first."
    fi
    if [[ ! -d "$GAME" ]]; then
        die "game/ directory not found. Run ./setup.sh first."
    fi
}

run_evennia() {
    local op="$1"
    # Run from game/ so Evennia picks up the correct gamedir
    if ! (cd "$GAME" && "$EVENNIA" "$op"); then
        local code=$?
        echo ""
        echo "Hint: run './game_ctl.sh status' to see the current server state."
        exit $code
    fi
}

usage() {
    grep '^#   ' "$0" | sed 's/^# //'
    exit 1
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_start() {
    echo "Starting Deku MUD ..."
    run_evennia start
}

cmd_stop() {
    echo "Stopping Deku MUD ..."
    run_evennia stop
}

cmd_reload() {
    echo "Reloading Deku MUD (hot-reload, no player disconnect) ..."
    run_evennia reload
}

cmd_restart() {
    echo "Rebooting Deku MUD (full stop + start) ..."
    run_evennia reboot
}

cmd_kill() {
    echo "Force-killing Deku MUD ..."
    run_evennia kill
}

cmd_status() {
    (cd "$GAME" && "$EVENNIA" status)
}

cmd_logs() {
    (cd "$GAME" && "$EVENNIA" --log)
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

check_setup

case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    reload)  cmd_reload  ;;
    restart|reboot) cmd_restart ;;
    kill)    cmd_kill    ;;
    status)  cmd_status  ;;
    logs)    cmd_logs    ;;
    *)       usage       ;;
esac

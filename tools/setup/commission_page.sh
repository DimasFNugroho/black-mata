#!/usr/bin/env bash
# commission_page.sh — Serve the Black-Mata v1 acceptance/commissioning page over HTTP.
#
# The checklist (docs/v1_acceptance_test.html) is a static page. Served over HTTP it
# auto-loads the sibling results JSON and is reachable from a phone/tablet on the same
# Tailnet — handy for walking around the robot while ticking items off.
#
# Usage:
#   bash tools/setup/commission_page.sh start [PORT]    # start server (default port 8088)
#   bash tools/setup/commission_page.sh stop            # stop server
#   bash tools/setup/commission_page.sh status          # is it running? show URLs
#   bash tools/setup/commission_page.sh restart [PORT]
#
# Notes:
#   - Binds 0.0.0.0 so it is reachable over Tailscale / LAN, not just localhost.
#   - Serves the docs/ folder only (the page + its results JSON live there).
#   - Stateless: results are saved in each browser's localStorage and via the page's
#     Save/Open (JSON) buttons. Stopping the server never touches saved results.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SERVE_DIR="${REPO_DIR}/docs"
PAGE="v1_acceptance_test.html"
DEFAULT_PORT=8088

RUN_DIR="${REPO_DIR}/.run"
PID_FILE="${RUN_DIR}/commission_page.pid"
PORT_FILE="${RUN_DIR}/commission_page.port"
LOG_FILE="${RUN_DIR}/commission_page.log"

# ── Helpers ──────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: bash tools/setup/commission_page.sh start|stop|status|restart [PORT]"
    exit 1
}

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

tailscale_ip() {
    command -v tailscale >/dev/null 2>&1 && tailscale ip -4 2>/dev/null | head -1 || true
}

print_urls() {
    local port="$1"
    echo "  Local:     http://localhost:${port}/${PAGE}"
    local ts; ts="$(tailscale_ip)"
    [ -n "$ts" ] && echo "  Tailscale: http://${ts}:${port}/${PAGE}"
}

# ── Commands ─────────────────────────────────────────────────────────────────

cmd_start() {
    local port="${1:-$DEFAULT_PORT}"

    if is_running; then
        echo "Already running (PID $(cat "$PID_FILE"), port $(cat "$PORT_FILE" 2>/dev/null || echo '?'))."
        print_urls "$(cat "$PORT_FILE" 2>/dev/null || echo "$port")"
        return 0
    fi

    [ -f "${SERVE_DIR}/${PAGE}" ] || { echo "ERROR: ${SERVE_DIR}/${PAGE} not found."; exit 1; }
    command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found."; exit 1; }

    mkdir -p "$RUN_DIR"

    # http.server has no built-in --directory in old Python; cd into the dir to be safe.
    ( cd "$SERVE_DIR" && exec python3 -m http.server "$port" --bind 0.0.0.0 ) \
        >"$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid"  > "$PID_FILE"
    echo "$port" > "$PORT_FILE"

    sleep 1
    if ! is_running; then
        echo "ERROR: server failed to start. Last log lines:"
        tail -n 20 "$LOG_FILE" 2>/dev/null || true
        rm -f "$PID_FILE" "$PORT_FILE"
        exit 1
    fi

    echo "Commissioning page served (PID ${pid}, port ${port})."
    print_urls "$port"
    echo "  Log:       ${LOG_FILE}"
}

cmd_stop() {
    if ! is_running; then
        echo "Not running."
        rm -f "$PID_FILE" "$PORT_FILE"
        return 0
    fi
    local pid; pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE" "$PORT_FILE"
    echo "Stopped (was PID ${pid})."
}

cmd_status() {
    if is_running; then
        local port; port="$(cat "$PORT_FILE" 2>/dev/null || echo "$DEFAULT_PORT")"
        echo "Running (PID $(cat "$PID_FILE"), port ${port})."
        print_urls "$port"
    else
        echo "Not running."
        return 1
    fi
}

# ── Dispatch ─────────────────────────────────────────────────────────────────

case "${1:-}" in
    start)   shift; cmd_start "${1:-}";;
    stop)    cmd_stop;;
    status)  cmd_status;;
    restart) shift; cmd_stop; cmd_start "${1:-}";;
    *)       usage;;
esac

#!/usr/bin/env bash
#
# push_gamepad_to_jetson.sh   (run on the x86 LAPTOP)
# ---------------------------------------------------------------------------
# scp the gamepad + dashboard tooling to the repo checkout on the Jetson so you
# can test on the real hardware before committing.
#
# With no arguments it sends the full runtime set for the gamepad/pairing
# feature (gamepad tooling + dashboard server + static UI). Pass explicit
# repo-relative paths to send just those instead.
#
# Usage (run from anywhere):
#     bash tools/gamepad/push_gamepad_to_jetson.sh
#     bash tools/gamepad/push_gamepad_to_jetson.sh tools/dashboard/server.py ...
#
# One-time: confirm JETSON and REPO_DIR below match your setup.
# ---------------------------------------------------------------------------
set -euo pipefail

# >>> CONFIRM THESE <<<
JETSON="mata-mata@100.111.193.124"      # same host as monitor.conf / flash.conf
REPO_DIR="~/Documents/black-mata"                  # path to the repo checkout ON the Jetson

# Resolve repo root from this script's location (tools/gamepad/ -> repo root).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }

# File list: default to the full gamepad/dashboard runtime set, or whatever
# paths were passed as args. Each entry is relative to the repo root so the
# remote path mirrors it exactly. (Calibrations are intentionally NOT pushed —
# they are device-specific and may be newer on the Jetson.)
if [ "$#" -gt 0 ]; then
    REL_FILES=( "$@" )
else
    REL_FILES=(
        # gamepad tooling
        "tools/gamepad/bt_setup.py"
        "tools/gamepad/setup_gamepad.sh"
        "tools/gamepad/setup_bluetooth_host.sh"
        "tools/gamepad/check_bluetooth_host.sh"
        "tools/gamepad/commission_bluetooth.sh"
        "tools/gamepad/reset_bluetooth_host.sh"
        "tools/gamepad/gamepad_core.py"
        "tools/gamepad/gamepad_test.py"
        # dashboard server + UI
        "tools/dashboard/server.py"
        "tools/dashboard/static/index.html"
        "tools/dashboard/static/app.js"
        "tools/dashboard/static/style.css"
        "tools/dashboard/static/setup_modal.html"
        "tools/dashboard/static/setup_modal.js"
    )
fi

# Verify every file exists locally before touching the network.
for f in "${REL_FILES[@]}"; do
    [ -f "${REPO_ROOT}/${f}" ] || die "missing local file: ${f}"
done

# Reuse ONE authenticated SSH connection for every ssh/scp below (SSH connection
# multiplexing), so the password is typed only once — no SSH key needed. A
# master connection is opened now and torn down when the script exits.
CTL="$(mktemp -u "${TMPDIR:-/tmp}/bm-push-XXXXXX")"
SSH_OPTS=(-o "ControlMaster=auto" -o "ControlPath=${CTL}" -o "ControlPersist=120")
cleanup() { ssh -o "ControlPath=${CTL}" -O exit "${JETSON}" 2>/dev/null || true; }
trap cleanup EXIT

echo "Connecting to ${JETSON} (enter the password once)..."
ssh "${SSH_OPTS[@]}" "${JETSON}" true \
    || die "could not connect to ${JETSON}"

# scp can't create missing parent dirs, so pre-create each needed remote dir.
# Collect the unique directory parts as a single SPACE-separated line — newlines
# would make the remote shell treat each dir after the first as its own command.
REMOTE_DIRS="$(printf '%s\n' "${REL_FILES[@]}" | xargs -n1 dirname | sort -u | tr '\n' ' ')"
echo "Ensuring remote directories exist under ${REPO_DIR} ..."
ssh "${SSH_OPTS[@]}" "${JETSON}" "cd ${REPO_DIR} && mkdir -p ${REMOTE_DIRS}" \
    || die "could not reach ${REPO_DIR} on ${JETSON} (edit REPO_DIR)"

# Copy each file to its mirrored path on the Jetson — all over the one master
# connection, so none of these prompt for a password.
for f in "${REL_FILES[@]}"; do
    echo "  -> ${f}"
    scp "${SSH_OPTS[@]}" "${REPO_ROOT}/${f}" "${JETSON}:${REPO_DIR}/${f}"
done

echo
echo "Done. Now ON THE JETSON:"
echo "    cd ${REPO_DIR}"
echo "    bash tools/gamepad/setup_bluetooth_host.sh     # once per robot (root commissioning)"
echo "    python3 tools/dashboard/server.py              # then pair from the GAMEPAD pill in the browser"
echo "    # or pair from the terminal:  bash tools/gamepad/setup_gamepad.sh [--verbose]"

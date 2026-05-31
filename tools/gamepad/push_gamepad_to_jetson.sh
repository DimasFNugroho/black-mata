#!/usr/bin/env bash
#
# push_gamepad_to_jetson.sh   (run on the x86 LAPTOP)
# ---------------------------------------------------------------------------
# scp the gamepad tooling files to the repo checkout on the Jetson so you can
# test the Phase G pairing cutover (bt_setup.py + thin-wrapper setup_gamepad.sh)
# on the real hardware before committing.
#
# By default it sends the Phase G files:
#     tools/gamepad/bt_setup.py
#     tools/gamepad/setup_gamepad.sh
#     tools/gamepad/gamepad_core.py     (verify() imports dev_path from it)
#
# Usage (run from anywhere):
#     bash tools/gamepad/push_gamepad_to_jetson.sh
#     bash tools/gamepad/push_gamepad_to_jetson.sh path/to/extra_file ...   # send specific files instead
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

# File list: default to the Phase G files, or whatever paths were passed as args.
# Each entry is relative to the repo root so the remote path mirrors it exactly.
if [ "$#" -gt 0 ]; then
    REL_FILES=( "$@" )
else
    REL_FILES=(
        "tools/gamepad/bt_setup.py"
        "tools/gamepad/setup_gamepad.sh"
        "tools/gamepad/gamepad_core.py"
    )
fi

# Verify every file exists locally before touching the network.
for f in "${REL_FILES[@]}"; do
    [ -f "${REPO_ROOT}/${f}" ] || die "missing local file: ${f}"
done

# scp can't create missing parent dirs, so pre-create each needed remote dir.
# Collect the unique directory parts of the file list.
REMOTE_DIRS="$(printf '%s\n' "${REL_FILES[@]}" | xargs -n1 dirname | sort -u)"
echo "Ensuring remote directories exist under ${REPO_DIR} ..."
# shellcheck disable=SC2086   # REMOTE_DIRS is intentionally word-split
ssh "${JETSON}" "cd ${REPO_DIR} && mkdir -p ${REMOTE_DIRS}" \
    || die "could not reach ${JETSON} or ${REPO_DIR} does not exist (edit REPO_DIR)"

# Copy each file to its mirrored path on the Jetson.
for f in "${REL_FILES[@]}"; do
    echo "  -> ${f}"
    scp "${REPO_ROOT}/${f}" "${JETSON}:${REPO_DIR}/${f}"
done

echo
echo "Done. Now ON THE JETSON:"
echo "    cd ${REPO_DIR}"
echo "    bash tools/gamepad/setup_gamepad.sh            # pair (add --verbose if it fails)"
echo "    python3 tools/gamepad/gamepad_test.py          # validate inputs"

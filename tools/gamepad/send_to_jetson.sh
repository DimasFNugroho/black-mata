#!/usr/bin/env bash
#
# send_to_jetson.sh  (run on the LAPTOP)
# ---------------------------------------------------------------------------
# Copies the RTL8761B btrtl fix script + firmware to the Jetson so you can run
# jetson_btrtl_8761b_fix.sh there.
#
#   1. Edit JETSON below (user@ip or user@hostname).
#   2. Run:  bash send_to_jetson.sh
#            bash send_to_jetson.sh --with-source   # also fetch+send btrtl.c/.h
#                                                   # (downloads ~1.5 GB on laptop,
#                                                   #  so the Jetson skips the download)
# ---------------------------------------------------------------------------
set -euo pipefail

# >>> EDIT THIS <<<  find it on the Jetson with:  hostname -I
JETSON="mata-mata@100.111.193.124"          # e.g. mata-mata@192.168.1.42  or  mata-mata@mata-desktop.local
DEST="~/"                              # where files land on the Jetson

# Resolve paths relative to this script so it works from anywhere.
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${HERE}/jetson_btrtl_8761b_fix.sh"
FW_FW="/tmp/rtl8761bu_fw.bin"
FW_CFG="/tmp/rtl8761bu_config.bin"
L4T_URL="https://developer.nvidia.com/embedded/l4t/r32_release_v7.6/sources/t210/public_sources.tbz2"

die() { echo "ERROR: $*" >&2; exit 1; }

[ "${JETSON}" != "mata-mata@CHANGE_ME" ] || die "edit JETSON= at the top of this script first"
[ -f "${SCRIPT}" ] || die "missing ${SCRIPT}"
[ -f "${FW_FW}" ]  || die "missing ${FW_FW} (re-decompress the rtl8761bu firmware)"
[ -f "${FW_CFG}" ] || die "missing ${FW_CFG}"

FILES=( "${SCRIPT}" "${FW_FW}" "${FW_CFG}" )

# Optional: fetch NVIDIA's btrtl.c/.h on the laptop and include them, so the
# Jetson doesn't have to download the big source tarball itself.
if [ "${1:-}" = "--with-source" ]; then
    WORK="$(mktemp -d)"
    echo "Downloading L4T sources to extract btrtl.c/.h (this is large)…"
    wget -O "${WORK}/public_sources.tbz2" "${L4T_URL}" || die "source download failed"
    tar xjf "${WORK}/public_sources.tbz2" -C "${WORK}" \
        Linux_for_Tegra/source/public/kernel_src.tbz2 || die "extract kernel_src.tbz2 failed"
    tar xjf "${WORK}/Linux_for_Tegra/source/public/kernel_src.tbz2" -C "${WORK}" \
        kernel/kernel-4.9/drivers/bluetooth/btrtl.c \
        kernel/kernel-4.9/drivers/bluetooth/btrtl.h || die "extract btrtl.{c,h} failed"
    FILES+=( "${WORK}/kernel/kernel-4.9/drivers/bluetooth/btrtl.c" \
             "${WORK}/kernel/kernel-4.9/drivers/bluetooth/btrtl.h" )
    echo "Including btrtl.c/.h in the transfer."
fi

echo "Copying to ${JETSON}:${DEST}"
scp "${FILES[@]}" "${JETSON}:${DEST}"

echo
echo "Done. Now on the Jetson run:"
echo "    cd ~ && bash jetson_btrtl_8761b_fix.sh"

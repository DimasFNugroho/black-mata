#!/usr/bin/env bash
#
# jetson_btrtl_8761b_fix.sh
# ---------------------------------------------------------------------------
# Make an RTL8761B USB Bluetooth dongle (e.g. "BT 6.0", USB id 0bda:a760)
# work on the Jetson Nano running L4T R32.7.x / kernel 4.9.337-tegra.
#
# Why this is needed:
#   NVIDIA backported a ~5.0/5.1-era btrtl into their 4.9 kernel. It loads
#   and parses the RTL8761B firmware fine, but its project_id[] table stops
#   before the 8761B entry (added upstream in 5.2), so it bails with:
#       Bluetooth: hci0: unknown project id 14
#   This script rebuilds btrtl.ko with that one entry added, plants the
#   8761BU firmware under the names the driver requests, and blacklists the
#   vendor rtk_btusb driver (which grabs the device first and fails).
#
# Usage:
#   1. Put btrtl.c + btrtl.h (R32.7.x source) next to this script, OR let the
#      script download the L4T sources itself (large, ~1.5 GB).
#   2. Put rtl8761bu_fw.bin + rtl8761bu_config.bin next to this script.
#   3. Run:  bash jetson_btrtl_8761b_fix.sh
#   4. Unplug + replug the dongle, then check: hciconfig -a ; btmgmt info
#
# Safe to re-run: it backs up originals and skips work already done.
# ---------------------------------------------------------------------------
set -uo pipefail

KREL="$(uname -r)"                         # expect 4.9.337-tegra
KBUILD="/lib/modules/${KREL}/build"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${HOME}/btrtl-8761b"
FW_DIR="/lib/firmware/rtl_bt"
MOD_DIR="/lib/modules/${KREL}/kernel/drivers/bluetooth"
L4T_URL="https://developer.nvidia.com/embedded/l4t/r32_release_v7.6/sources/t210/public_sources.tbz2"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

say "Jetson RTL8761B btrtl fix  (kernel ${KREL})"

# --- 0. sanity checks --------------------------------------------------------
[ -d "${KBUILD}" ] || die "kernel headers missing at ${KBUILD}"
command -v make >/dev/null || die "make not found"
command -v gcc  >/dev/null || die "gcc not found"
case "${KREL}" in
  4.9.*-tegra) : ;;
  *) echo "WARNING: expected 4.9.x-tegra, got ${KREL} — continuing anyway." ;;
esac

mkdir -p "${WORK}"
cd "${WORK}" || die "cannot cd ${WORK}"

# --- 1. obtain btrtl.c / btrtl.h --------------------------------------------
if [ -f "${SCRIPT_DIR}/btrtl.c" ] && [ -f "${SCRIPT_DIR}/btrtl.h" ]; then
    say "Using btrtl.c/.h provided next to the script"
    cp "${SCRIPT_DIR}/btrtl.c" "${SCRIPT_DIR}/btrtl.h" "${WORK}/"
elif [ -f "${WORK}/btrtl.c" ] && [ -f "${WORK}/btrtl.h" ]; then
    say "Reusing btrtl.c/.h already in ${WORK}"
else
    say "Downloading L4T sources (large) to extract btrtl.c/.h"
    command -v wget >/dev/null || die "wget not found and no btrtl.c/.h provided"
    wget -O public_sources.tbz2 "${L4T_URL}" || die "download failed; scp btrtl.c/.h instead"
    tar xjf public_sources.tbz2 Linux_for_Tegra/source/public/kernel_src.tbz2 \
        || die "could not extract kernel_src.tbz2"
    tar xjf Linux_for_Tegra/source/public/kernel_src.tbz2 \
        kernel/kernel-4.9/drivers/bluetooth/btrtl.c \
        kernel/kernel-4.9/drivers/bluetooth/btrtl.h \
        || die "could not extract btrtl.{c,h}"
    cp kernel/kernel-4.9/drivers/bluetooth/btrtl.c "${WORK}/"
    cp kernel/kernel-4.9/drivers/bluetooth/btrtl.h "${WORK}/"
fi
[ -f btrtl.c ] && [ -f btrtl.h ] || die "btrtl.c/.h not available"

# --- 2. patch: add RTL8761B (project id 14) ---------------------------------
say "project_id[] table BEFORE patch:"
grep -n "project_id\[\]" -A 40 btrtl.c | sed -n '1,45p' || true

if grep -q "RTL_ROM_LMP_8761A,[[:space:]]*14" btrtl.c; then
    echo "Patch already present — skipping."
else
    python3 - "btrtl.c" <<'PY' || die "python patch step failed"
import re, sys
path = sys.argv[1]
src = open(path).read()
# NVIDIA's 4.9 btrtl names this table project_id_to_lmp_subver[]; mainline
# uses project_id[]. Match either.
m = re.search(r"(\w*project_id\w*\[\]\s*=\s*\{)(.*?)(\};)", src, re.S)
if not m:
    sys.exit("could not locate project-id table")
body = m.group(2).rstrip()
entry = "\n\t{ RTL_ROM_LMP_8761A, 14 },\t/* RTL8761B (added for Jetson) */\n"
src = src[:m.start(2)] + body + entry + src[m.end(2):]
open(path, "w").write(src)
print("Inserted RTL8761B project id 14 entry.")
PY
fi

say "project_id[] table AFTER patch:"
grep -n "project_id\[\]" -A 40 btrtl.c | sed -n '1,45p' || true

# --- 3. build btrtl.ko out-of-tree ------------------------------------------
cat > Makefile <<EOF
obj-m += btrtl.o
all:
	\$(MAKE) -C ${KBUILD} M=\$(PWD) modules
clean:
	\$(MAKE) -C ${KBUILD} M=\$(PWD) clean
EOF

say "Building btrtl.ko"
make clean >/dev/null 2>&1 || true
make || die "module build failed — paste the compiler output"
[ -f btrtl.ko ] || die "build produced no btrtl.ko"
echo "Built: $(ls -la btrtl.ko)"
modinfo ./btrtl.ko | grep -E "vermagic|filename" || true

# --- 4. plant firmware under the names the driver requests -------------------
# The in-kernel ic_id_table maps lmp_subver=0x8761 -> rtl8761a_*; feeding the
# 8761BU firmware under those names is the proven-loadable path on this kernel.
say "Installing firmware into ${FW_DIR}"
sudo mkdir -p "${FW_DIR}"
for f in rtl8761bu_fw.bin rtl8761bu_config.bin; do
    if [ -f "${SCRIPT_DIR}/${f}" ]; then
        sudo cp "${SCRIPT_DIR}/${f}" "${FW_DIR}/${f}"
    fi
done
[ -f "${FW_DIR}/rtl8761bu_fw.bin" ] || die "rtl8761bu_fw.bin missing — scp it next to the script"
[ -f "${FW_DIR}/rtl8761bu_config.bin" ] || die "rtl8761bu_config.bin missing — scp it next to the script"

# back up the genuine 8761a firmware once, then override with 8761bu
if [ -f "${FW_DIR}/rtl8761a_fw.bin" ] && [ ! -f "${FW_DIR}/rtl8761a_fw.bin.orig" ]; then
    sudo cp "${FW_DIR}/rtl8761a_fw.bin" "${FW_DIR}/rtl8761a_fw.bin.orig"
fi
sudo cp "${FW_DIR}/rtl8761bu_fw.bin"     "${FW_DIR}/rtl8761a_fw.bin"
sudo cp "${FW_DIR}/rtl8761bu_config.bin" "${FW_DIR}/rtl8761a_config.bin"

# --- 5. blacklist the vendor rtk_btusb driver -------------------------------
say "Blacklisting vendor rtk_btusb (it grabs the device and fails)"
echo "blacklist rtk_btusb" | sudo tee /etc/modprobe.d/blacklist-rtk_btusb.conf >/dev/null
sudo modprobe -r rtk_btusb 2>/dev/null || true

# --- 6. install the patched module ------------------------------------------
say "Installing patched btrtl.ko into ${MOD_DIR}"
[ -d "${MOD_DIR}" ] || die "module dir ${MOD_DIR} missing"
if [ -f "${MOD_DIR}/btrtl.ko" ] && [ ! -f "${MOD_DIR}/btrtl.ko.orig" ]; then
    sudo cp "${MOD_DIR}/btrtl.ko" "${MOD_DIR}/btrtl.ko.orig"
fi
sudo cp btrtl.ko "${MOD_DIR}/btrtl.ko"
sudo depmod -a

# --- 7. reload the stack -----------------------------------------------------
say "Reloading bluetooth modules"
sudo modprobe -r btusb 2>/dev/null || true
sudo modprobe -r btrtl 2>/dev/null || true
sudo modprobe btrtl || die "failed to load patched btrtl"
sudo modprobe btusb || die "failed to load btusb"
sudo systemctl restart bluetooth 2>/dev/null || true

cat <<EOF

============================================================================
 Build + install done.

 Now UNPLUG and REPLUG the dongle, then run:

     dmesg | grep -i rtl | tail
     hciconfig -a
     btmgmt info

 SUCCESS looks like:
   - dmesg: "rtl: fw version ..." with NO "unknown project id"
   - hciconfig: a real (non-zero) BD Address
   - btmgmt info: 1 controller, real addr

 If it still fails, paste the three outputs above.

 Rollback if ever needed:
     sudo cp ${MOD_DIR}/btrtl.ko.orig ${MOD_DIR}/btrtl.ko
     sudo mv ${FW_DIR}/rtl8761a_fw.bin.orig ${FW_DIR}/rtl8761a_fw.bin
     sudo rm -f /etc/modprobe.d/blacklist-rtk_btusb.conf
     sudo depmod -a
============================================================================
EOF

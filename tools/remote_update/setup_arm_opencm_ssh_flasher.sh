#!/usr/bin/env bash
set -euo pipefail

INSTALL_PATH="/usr/local/bin/opencm9.04_ld_armhf"
RULE_PATH="/etc/udev/rules.d/99-opencm.rules"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 2
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script currently supports Debian/Ubuntu (apt-get)." >&2
  exit 2
fi

echo "[1/5] Enabling armhf multiarch (if needed)"
if ! dpkg --print-foreign-architectures | grep -qx armhf; then
  dpkg --add-architecture armhf
fi

echo "[2/5] Installing armhf runtime dependency"
apt-get update
apt-get install -y libc6:armhf

echo "[3/5] Downloading official OpenCM ARM uploader binary"
curl -fL \
  "https://raw.githubusercontent.com/ROBOTIS-GIT/OpenCM9.04/master/arduino/opencm_arduino/tools/opencm_tools_0.0.2/arm/opencm9.04_ld" \
  -o "$TMP_DIR/opencm9.04_ld"

echo "[4/5] Installing uploader to $INSTALL_PATH"
install -m 0755 "$TMP_DIR/opencm9.04_ld" "$INSTALL_PATH"

echo "[5/5] Installing persistent /dev/opencm udev alias"
cat > "$RULE_PATH" <<'RULE'
# Stable alias for OpenCM (ROBOTIS ComPort)
SUBSYSTEM=="tty", ATTRS{idVendor}=="fff1", ATTRS{idProduct}=="ff48", SYMLINK+="opencm", GROUP="dialout", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
RULE
udevadm control --reload-rules
udevadm trigger || true

echo
echo "Installed: $INSTALL_PATH"
echo "udev rule: $RULE_PATH"
echo "Recommended stable port: /dev/opencm"
echo "Quick check:"
echo "  ls -l /dev/opencm /dev/serial/by-id"
echo "  $INSTALL_PATH /dev/opencm 57600 /path/to/file.bin 1 opencm"

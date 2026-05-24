#!/usr/bin/env python3
"""
setup_udev.py — Install a udev rule for the robot camera.

Gives the camera a stable symlink at /dev/robot_camera so the device
enumeration order (/dev/video0 vs /dev/video1) no longer matters.

The rule is tied to the camera's USB vendor and product ID, so the same
physical camera always gets the same path regardless of port or boot order.

Usage:
    sudo python3 tools/camera/setup_udev.py                  # auto-detect
    sudo python3 tools/camera/setup_udev.py --device /dev/video0
    sudo python3 tools/camera/setup_udev.py --dry-run         # preview only

Deployment:
    Run once per robot after OS install. Re-run if the camera hardware changes.
"""

import argparse
import glob
import os
import subprocess
import sys


RULE_FILE = '/etc/udev/rules.d/99-robot-camera.rules'
SYMLINK   = 'robot_camera'


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_usb_ids(device: str):
    """Return (vendor_id, product_id) for a /dev/videoX device, or (None, None)."""
    try:
        out = subprocess.check_output(
            ['udevadm', 'info', '--name', device, '--attribute-walk'],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        )
    except subprocess.CalledProcessError:
        return None, None

    vendor = None
    product = None
    # Walk up the device tree; first match is the closest (most specific) ancestor
    for line in out.splitlines():
        line = line.strip()
        if 'ATTRS{idVendor}' in line and vendor is None:
            vendor = line.split('==')[1].strip('"')
        if 'ATTRS{idProduct}' in line and product is None:
            product = line.split('==')[1].strip('"')
        if vendor and product:
            break
    return vendor, product


def find_usb_video_devices():
    """Return list of (device_path, vendor_id, product_id) for all USB cameras."""
    found = []
    for dev in sorted(glob.glob('/dev/video*')):
        vendor, product = get_usb_ids(dev)
        if vendor and product:
            found.append((dev, vendor, product))
    return found


def pick_device(specified: str = None):
    """Return (device_path, vendor_id, product_id) based on user choice or auto-detect."""
    usb_devices = find_usb_video_devices()

    if not usb_devices:
        print('ERROR: No USB video devices found under /dev/video*.')
        print('       Is the camera connected?')
        sys.exit(1)

    if specified:
        match = [(d, v, p) for d, v, p in usb_devices if d == specified]
        if not match:
            available = [d for d, _, _ in usb_devices]
            print(f'ERROR: {specified} not found among USB video devices.')
            print(f'       Available: {available}')
            sys.exit(1)
        return match[0]

    if len(usb_devices) == 1:
        dev, vendor, product = usb_devices[0]
        print(f'Auto-detected: {dev}  (idVendor={vendor}, idProduct={product})')
        return usb_devices[0]

    # Multiple cameras — ask the user
    print('Multiple USB video devices found:')
    for i, (dev, vendor, product) in enumerate(usb_devices):
        print(f'  [{i}] {dev}  idVendor={vendor}  idProduct={product}')
    choice = input('Select the robot camera [0]: ').strip() or '0'
    try:
        return usb_devices[int(choice)]
    except (ValueError, IndexError):
        print('Invalid selection.')
        sys.exit(1)


def build_rule(vendor: str, product: str) -> str:
    return (
        f'# Robot camera — stable symlink at /dev/{SYMLINK}\n'
        f'SUBSYSTEM=="video4linux", '
        f'ATTRS{{idVendor}}=="{vendor}", '
        f'ATTRS{{idProduct}}=="{product}", '
        f'SYMLINK+="{SYMLINK}"\n'
    )


def write_rule(rule: str) -> None:
    with open(RULE_FILE, 'w') as f:
        f.write(rule)
    print(f'Written:  {RULE_FILE}')


def reload_udev() -> None:
    subprocess.run(['udevadm', 'control', '--reload-rules'], check=True)
    subprocess.run(['udevadm', 'trigger'], check=True)
    print('udev rules reloaded.')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Install udev symlink rule for the robot camera'
    )
    parser.add_argument(
        '--device', '-d',
        help='Camera device to use (e.g. /dev/video0). Auto-detects if omitted.'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print the rule that would be written without installing it.'
    )
    args = parser.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print('ERROR: This script must be run with sudo.')
        print(f'       sudo python3 {sys.argv[0]}')
        sys.exit(1)

    device, vendor, product = pick_device(args.device)
    rule = build_rule(vendor, product)

    print(f'\nDevice:   {device}')
    print(f'Vendor:   {vendor}')
    print(f'Product:  {product}')
    print(f'Symlink:  /dev/{SYMLINK}')
    print(f'\nRule:\n  {rule.strip()}\n')

    if args.dry_run:
        print('[dry-run] No changes made.')
        return

    write_rule(rule)
    reload_udev()

    print(f'\nDone. Reconnect the camera — it will appear at /dev/{SYMLINK}')
    print(f'Verify with: ls -l /dev/{SYMLINK}')


if __name__ == '__main__':
    main()

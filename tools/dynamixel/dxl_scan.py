#!/usr/bin/env python3
"""
dxl_scan.py

Scans the Dynamixel bus for all connected servos.
Tries the specified baud rate (default 1 Mbps) and optionally
additional common rates with --all-bauds.

Usage:
    python3 dxl_scan.py
    python3 dxl_scan.py --port /dev/ttyACM0
    python3 dxl_scan.py --all-bauds

Requires the OpenCM9.04 to be running dxl_u2d2_bridge.ino.
"""

import sys
from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS
from dxl_common import (
    open_port, BAUD_RATE, PROTOCOL,
    ADDR_CW_ANGLE_LIMIT, ADDR_CCW_ANGLE_LIMIT,
    is_wheel_mode, port_arg
)

COMMON_BAUDS = [1_000_000, 115_200, 57_600, 19_200, 9_600]


def scan(ph: PortHandler, pkt: PacketHandler, baud: int) -> int:
    ph.setBaudRate(baud)
    print(f"\n  Baud rate: {baud}")
    found = 0
    for servo_id in range(1, 253):
        model, result, _ = pkt.ping(ph, servo_id)
        if result == COMM_SUCCESS:
            found += 1
            # Read mode
            cw,  r1, _ = pkt.read2ByteTxRx(ph, servo_id, ADDR_CW_ANGLE_LIMIT)
            ccw, r2, _ = pkt.read2ByteTxRx(ph, servo_id, ADDR_CCW_ANGLE_LIMIT)
            mode = "WHEEL" if (r1 == 0 and r2 == 0 and is_wheel_mode(cw, ccw)) else "JOINT"
            print(f"    [FOUND] ID: {servo_id:3d}  Model: {model:6d}  Mode: {mode}")
    if found == 0:
        print("    No servos found at this baud rate.")
    else:
        print(f"    Total found: {found}")
    return found


def main():
    p = port_arg("Scan Dynamixel bus for connected servos")
    p.add_argument("--all-bauds", action="store_true",
                   help="Try common baud rates in addition to --baud")
    args = p.parse_args()

    ph, pkt = open_port(args.port, args.baud)

    print("==============================================")
    print(" Dynamixel ID Scanner (Python / DynamixelSDK)")
    print(f" Port    : {args.port}")
    print(f" Protocol: {PROTOCOL}")
    print("==============================================")

    bauds = COMMON_BAUDS if args.all_bauds else [args.baud]

    for baud in bauds:
        scan(ph, pkt, baud)

    ph.closePort()
    print("\nScan complete.")


if __name__ == "__main__":
    main()

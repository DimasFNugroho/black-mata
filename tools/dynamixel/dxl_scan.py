#!/usr/bin/env python3
"""
dxl_scan.py

Scans the Dynamixel bus for all connected servos.
Sends SCAN command to the OpenCM9.04 running dxl_commander.ino.

Usage:
    python3 dxl_scan.py
    python3 dxl_scan.py --port /dev/opencm
    python3 dxl_scan.py --max-id 30
"""

from dxl_common import open_port, send_cmd, read_until_ok, port_arg


def main():
    p = port_arg("Scan Dynamixel bus for connected servos")
    p.add_argument("--max-id", type=int, default=252,
                   help="Max servo ID to scan (default: 252)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    print("==============================================")
    print(" Dynamixel ID Scanner")
    print("==============================================")

    send_cmd(ser, "SCAN {}".format(args.max_id))
    lines, ok_line = read_until_ok(ser, prefix="OK,SCAN", timeout=60)

    for line in lines:
        if line.startswith("FOUND,"):
            parts = line.split(",")
            if len(parts) >= 5:
                print("  [FOUND] ID: {:>3s}  Model: {:>6s}  FW: {}  Mode: {}".format(
                    parts[1], parts[2], parts[3], parts[4]))

    if ok_line and ok_line.startswith("OK,SCAN"):
        count = ok_line.split(",")[2] if len(ok_line.split(",")) > 2 else "?"
        print("\nTotal found: {}".format(count))
    elif ok_line and ok_line.startswith("ERR"):
        print("\nError: {}".format(ok_line))

    ser.close()


if __name__ == "__main__":
    main()

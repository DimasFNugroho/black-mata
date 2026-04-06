#!/usr/bin/env python3
"""
dxl_id_change.py

Changes a Dynamixel servo's ID. Validates, checks for conflicts,
and verifies the change.

WARNING: ID is stored in EEPROM. Do not power off during the write.

Usage:
    python3 dxl_id_change.py --current 1 --new 5
    python3 dxl_id_change.py --current 1 --new 5 --port /dev/opencm
"""

import sys
from dxl_common import open_port, send_cmd, read_until_ok, port_arg


def main():
    p = port_arg("Change a Dynamixel servo ID")
    p.add_argument("--current", "-c", type=int, required=True,
                   help="Current servo ID")
    p.add_argument("--new", "-n", type=int, required=True,
                   help="New servo ID to assign")
    args = p.parse_args()

    print("==============================================")
    print(" Dynamixel ID Change")
    print("==============================================")
    print("Current ID : {}".format(args.current))
    print("New ID     : {}".format(args.new))

    if not (1 <= args.new <= 252):
        print("ERROR: New ID must be between 1 and 252.", file=sys.stderr)
        sys.exit(1)
    if args.current == args.new:
        print("ERROR: Current ID and new ID are the same.", file=sys.stderr)
        sys.exit(1)

    ser = open_port(args.port, args.baud)

    send_cmd(ser, "IDCHANGE {} {}".format(args.current, args.new))
    _, ok_line = read_until_ok(ser, prefix="OK,IDCHANGE", timeout=10)

    if ok_line and ok_line.startswith("OK,IDCHANGE"):
        parts = ok_line.split(",")
        print("\nSUCCESS: Servo ID changed from {} to {}.".format(parts[2], parts[3]))
    elif ok_line:
        print("\nFailed: {}".format(ok_line), file=sys.stderr)
        ser.close()
        sys.exit(1)
    else:
        print("\nFailed: No response from OpenCM.", file=sys.stderr)
        ser.close()
        sys.exit(1)

    ser.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
dxl_mode.py

Read or change the operating mode (JOINT / WHEEL) of a Dynamixel servo.
Sends GETMODE / SETMODE command to OpenCM9.04 running dxl_commander.ino.

Usage:
    python3 dxl_mode.py --id 1                  # read mode
    python3 dxl_mode.py --id 1 --set WHEEL      # switch to wheel mode
    python3 dxl_mode.py --id 1 --set JOINT      # switch to joint mode
"""

import sys
from dxl_common import open_port, send_cmd, read_line, port_arg


def main():
    p = port_arg("Read or set Dynamixel servo operating mode")
    p.add_argument("--id", "-i", type=int, required=True,
                   help="Servo ID")
    p.add_argument("--set", "-s", choices=["JOINT", "WHEEL", "joint", "wheel"],
                   default=None, help="Set mode (omit to read)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    if args.set is None:
        # Read mode
        send_cmd(ser, "GETMODE {}".format(args.id))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,GETMODE"):
            parts = line.split(",")
            print("Servo {}: mode={} (CW_limit={}, CCW_limit={})".format(
                parts[2], parts[3], parts[4], parts[5]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)
    else:
        # Set mode
        mode = args.set.upper()
        send_cmd(ser, "SETMODE {} {}".format(args.id, mode))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,SETMODE"):
            parts = line.split(",")
            print("Servo {}: mode set to {}".format(parts[2], parts[3]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)

    ser.close()


if __name__ == "__main__":
    main()

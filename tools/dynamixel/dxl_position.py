#!/usr/bin/env python3
"""
dxl_position.py

Read or set the position of a Dynamixel servo.
Sends GETPOS / SETPOS command to OpenCM9.04 running dxl_commander.ino.

Usage:
    python3 dxl_position.py --id 1                  # read position
    python3 dxl_position.py --id 1 --set 512         # move to tick 512 (150 deg)
    python3 dxl_position.py --id 1 --set 512 --speed 100
"""

import sys
from dxl_common import open_port, send_cmd, read_line, port_arg


def main():
    p = port_arg("Read or set Dynamixel servo position")
    p.add_argument("--id", "-i", type=int, required=True,
                   help="Servo ID")
    p.add_argument("--set", "-s", type=int, default=None,
                   help="Set position in ticks 0-1023 (omit to read)")
    p.add_argument("--speed", type=int, default=200,
                   help="Moving speed in ticks 1-1023 (default: 200)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    if args.set is None:
        # Read position
        send_cmd(ser, "GETPOS {}".format(args.id))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,GETPOS"):
            parts = line.split(",")
            print("Servo {}: {} ticks ({} deg)".format(parts[2], parts[3], parts[4]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)
    else:
        # Set position
        if not (0 <= args.set <= 1023):
            print("ERROR: Position must be 0-1023 ticks.", file=sys.stderr)
            ser.close()
            sys.exit(1)
        send_cmd(ser, "SETPOS {} {} {}".format(args.id, args.set, args.speed))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,SETPOS"):
            parts = line.split(",")
            print("Servo {}: moving to tick {}".format(parts[2], parts[3]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)

    ser.close()


if __name__ == "__main__":
    main()

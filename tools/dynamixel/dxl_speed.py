#!/usr/bin/env python3
"""
dxl_speed.py

Read or set the speed of a Dynamixel servo.
Sends GETSPEED / SETSPEED command to OpenCM9.04 running dxl_commander.ino.

Usage:
    python3 dxl_speed.py --id 1                # read speed
    python3 dxl_speed.py --id 1 --set 200      # set speed
"""

import sys
from dxl_common import open_port, send_cmd, read_line, port_arg


def main():
    p = port_arg("Read or set Dynamixel servo speed")
    p.add_argument("--id", "-i", type=int, required=True,
                   help="Servo ID")
    p.add_argument("--set", "-s", type=int, default=None,
                   help="Set speed in ticks 0-2047 (omit to read)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    if args.set is None:
        # Read speed
        send_cmd(ser, "GETSPEED {}".format(args.id))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,GETSPEED"):
            parts = line.split(",")
            print("Servo {}: {} raw ({} RPM)".format(parts[2], parts[3], parts[4]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)
    else:
        # Set speed
        if not (0 <= args.set <= 2047):
            print("ERROR: Speed must be 0-2047.", file=sys.stderr)
            ser.close()
            sys.exit(1)
        send_cmd(ser, "SETSPEED {} {}".format(args.id, args.set))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,SETSPEED"):
            parts = line.split(",")
            print("Servo {}: speed set to {}".format(parts[2], parts[3]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)

    ser.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
dxl_voltage.py

Reads input voltage from one or all Dynamixel servos.
Sends VOLTAGE / VOLTAGES command to OpenCM9.04 running dxl_commander.ino.

Usage:
    python3 dxl_voltage.py                # all servos
    python3 dxl_voltage.py --id 1         # single servo
    python3 dxl_voltage.py --max-id 30    # scan up to ID 30
"""

import sys
from dxl_common import open_port, send_cmd, read_until_ok, read_line, port_arg


def main():
    p = port_arg("Read Dynamixel servo voltage")
    p.add_argument("--id", "-i", type=int, default=None,
                   help="Single servo ID (omit to read all)")
    p.add_argument("--max-id", type=int, default=252,
                   help="Max ID to scan when reading all (default: 252)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    if args.id is not None:
        # Single servo
        send_cmd(ser, "VOLTAGE {}".format(args.id))
        line = read_line(ser, timeout=5.0)
        if line and line.startswith("OK,VOLTAGE"):
            parts = line.split(",")
            print("Servo {}: {} V".format(parts[2], parts[3]))
        elif line:
            print("Error: {}".format(line), file=sys.stderr)
            ser.close()
            sys.exit(1)
    else:
        # All servos
        print("==============================================")
        print(" Dynamixel Servo Voltages")
        print("==============================================")

        send_cmd(ser, "VOLTAGES {}".format(args.max_id))
        lines, ok_line = read_until_ok(ser, prefix="OK,VOLTAGES", timeout=60)

        for line in lines:
            if line.startswith("VOLTAGE,"):
                parts = line.split(",")
                if len(parts) >= 3:
                    print("  Servo {:>3s}: {} V".format(parts[1], parts[2]))

        if ok_line and ok_line.startswith("OK,VOLTAGES"):
            count = ok_line.split(",")[2] if len(ok_line.split(",")) > 2 else "?"
            print("\nTotal: {} servo(s)".format(count))

    ser.close()


if __name__ == "__main__":
    main()

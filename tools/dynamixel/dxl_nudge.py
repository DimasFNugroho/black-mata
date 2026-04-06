#!/usr/bin/env python3
"""
dxl_nudge.py

Moves a servo +N degrees from its current position, waits, then returns.
Sends NUDGE command to the OpenCM9.04 running dxl_commander.ino.

If the servo is in WHEEL mode, it is temporarily switched to JOINT mode.

Usage:
    python3 dxl_nudge.py --id 1
    python3 dxl_nudge.py --id 1 --nudge 10 --speed 200
    python3 dxl_nudge.py --id 1 --repeat 3
"""

import sys
import time
from dxl_common import open_port, send_cmd, read_until_ok, port_arg


def do_nudge(ser, servo_id, nudge_deg, speed):
    """Run one nudge cycle. Returns True on success."""
    send_cmd(ser, "NUDGE {} {} {}".format(servo_id, nudge_deg, speed))
    lines, ok_line = read_until_ok(ser, prefix="OK,NUDGE", timeout=15)

    for line in lines:
        if line.startswith("NUDGE,"):
            parts = line.split(",")
            if len(parts) >= 3:
                print("  {}: {} deg".format(parts[1], parts[2]))

    if ok_line and ok_line.startswith("OK"):
        return True
    if ok_line:
        print("Error: {}".format(ok_line), file=sys.stderr)
    return False


def main():
    p = port_arg("Nudge a Dynamixel servo +N degrees and back")
    p.add_argument("--id", "-i", type=int, required=True, help="Servo ID")
    p.add_argument("--nudge", "-n", type=float, default=5.0,
                   help="Degrees to nudge (default: 5.0)")
    p.add_argument("--speed", "-s", type=int, default=200,
                   help="Goal speed in ticks 1-1023 (default: 200)")
    p.add_argument("--repeat", "-r", type=int, default=1,
                   help="Number of nudge cycles (default: 1)")
    p.add_argument("--interval", type=float, default=3.0,
                   help="Seconds between cycles (default: 3.0)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    print("==============================================")
    print(" Dynamixel Servo Nudge")
    print(" Servo ID: {}  Nudge: +/-{} deg".format(args.id, args.nudge))
    print("==============================================")

    try:
        for i in range(args.repeat):
            if args.repeat > 1:
                print("\n--- Cycle {}/{} ---".format(i + 1, args.repeat))
            if not do_nudge(ser, args.id, args.nudge, args.speed):
                break
            if i < args.repeat - 1:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()

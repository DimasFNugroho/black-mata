#!/usr/bin/env python3
"""
dxl_monitor.py

Continuously monitors a Dynamixel servo and streams its state as CSV.
Sends MONITOR command to the OpenCM9.04 running dxl_commander.ino.

Output format:
    STATUS,<ms>,<id>,<mode>,<pos_deg>,<speed_rpm>,<load_pct>,<voltage_V>,<temp_C>

Usage:
    python3 dxl_monitor.py --id 1
    python3 dxl_monitor.py --id 1 --interval 100
    python3 dxl_monitor.py --id 1 --port /dev/opencm
"""

import sys
from dxl_common import open_port, send_cmd, read_line, port_arg


def main():
    p = port_arg("Stream Dynamixel servo state as CSV")
    p.add_argument("--id", "-i", type=int, required=True,
                   help="Servo ID to monitor")
    p.add_argument("--interval", type=int, default=200,
                   help="Polling interval in ms (default: 200)")
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    print("# ==============================================")
    print("# Dynamixel Servo Monitor")
    print("# Servo ID: {}  Interval: {}ms".format(args.id, args.interval))
    print("# Press Ctrl+C to stop")
    print("# ==============================================")

    send_cmd(ser, "MONITOR {} {}".format(args.id, args.interval))

    try:
        while True:
            line = read_line(ser, timeout=5.0)
            if line is None:
                continue
            if line.startswith("ERR"):
                print(line, file=sys.stderr)
                break
            if line.startswith("OK,MONITOR"):
                break
            print(line, flush=True)

    except KeyboardInterrupt:
        # Send a character to stop MONITOR on the OpenCM
        ser.write(b"\n")
        ser.flush()
        print("\n# Stopped.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()

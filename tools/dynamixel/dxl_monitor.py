#!/usr/bin/env python3
"""
dxl_monitor.py

Continuously monitors a Dynamixel AX-12A servo and streams its
state as CSV lines to stdout.

Output format:
    STATUS,<ms>,<id>,<mode>,<position_deg>,<speed_rpm>,<load_pct>,<voltage_V>,<temp_C>

Usage:
    python3 dxl_monitor.py --id 1
    python3 dxl_monitor.py --id 1 --port /dev/ttyACM0 --interval 0.2

Requires the OpenCM9.04 to be running dxl_u2d2_bridge.ino.
"""

import sys
import time
from dxl_common import (
    open_port, read1, read2,
    ticks_to_deg, ticks_to_rpm, ticks_to_load_pct, is_wheel_mode,
    ADDR_CW_ANGLE_LIMIT, ADDR_CCW_ANGLE_LIMIT,
    ADDR_PRESENT_POSITION, ADDR_PRESENT_SPEED, ADDR_PRESENT_LOAD,
    ADDR_PRESENT_VOLTAGE, ADDR_PRESENT_TEMPERATURE,
    port_arg
)


def get_mode(pkt, ph, servo_id: int) -> str:
    cw  = read2(pkt, ph, servo_id, ADDR_CW_ANGLE_LIMIT)
    ccw = read2(pkt, ph, servo_id, ADDR_CCW_ANGLE_LIMIT)
    if cw is None or ccw is None:
        return "UNKNOWN"
    return "WHEEL" if is_wheel_mode(cw, ccw) else "JOINT"


def main():
    p = port_arg("Stream AX-12A servo state as CSV")
    p.add_argument("--id", "-i", type=int, required=True,
                   help="Servo ID to monitor")
    p.add_argument("--interval", type=float, default=0.2,
                   help="Polling interval in seconds (default: 0.2)")
    args = p.parse_args()

    ph, pkt = open_port(args.port, args.baud)
    servo_id = args.id

    print(f"# ==============================================")
    print(f"# Dynamixel Servo Monitor (Python / DynamixelSDK)")
    print(f"# Port    : {args.port}")
    print(f"# Servo ID: {servo_id}")
    print(f"# Interval: {args.interval}s")
    print(f"# ==============================================")
    print(f"# FORMAT: STATUS,ms,id,mode,position_deg,speed_rpm,load_pct,voltage_V,temp_C")
    print(f"#")

    # Ping first
    model, result, _ = pkt.ping(ph, servo_id)
    if result != 0:
        print(f"# ERROR: servo ID {servo_id} not found. Check wiring and ID.", file=sys.stderr)
        ph.closePort()
        sys.exit(1)
    print(f"# Model   : {model}")
    print(f"# Mode    : {get_mode(pkt, ph, servo_id)}")
    print(f"#")

    t0 = time.time()
    try:
        while True:
            ms = int((time.time() - t0) * 1000)

            pos  = read2(pkt, ph, servo_id, ADDR_PRESENT_POSITION)
            spd  = read2(pkt, ph, servo_id, ADDR_PRESENT_SPEED)
            load = read2(pkt, ph, servo_id, ADDR_PRESENT_LOAD)
            volt = read1(pkt, ph, servo_id, ADDR_PRESENT_VOLTAGE)
            temp = read1(pkt, ph, servo_id, ADDR_PRESENT_TEMPERATURE)
            mode = get_mode(pkt, ph, servo_id)

            if pos is None:
                print(f"# ERROR: lost connection to servo {servo_id}.", file=sys.stderr)
                break

            print(
                f"STATUS,{ms},{servo_id},{mode},"
                f"{ticks_to_deg(pos):.2f},"
                f"{ticks_to_rpm(spd if spd is not None else 0):.2f},"
                f"{ticks_to_load_pct(load if load is not None else 0):.2f},"
                f"{(volt or 0) * 0.1:.1f},"
                f"{temp or 0}",
                flush=True
            )

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n# Stopped.")
    finally:
        ph.closePort()


if __name__ == "__main__":
    main()

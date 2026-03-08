#!/usr/bin/env python3
"""
dxl_nudge.py

Moves an AX-12A servo +NUDGE_DEG degrees from its current position,
waits for motion to complete, then moves back to the original position.
Repeats every --interval seconds.

If the servo is in WHEEL mode, it is temporarily switched to JOINT
mode for the nudge, then restored to WHEEL mode afterwards.

Usage:
    python3 dxl_nudge.py --id 1
    python3 dxl_nudge.py --id 1 --nudge 10 --speed 200 --interval 5
    python3 dxl_nudge.py --id 1 --once

Requires the OpenCM9.04 to be running dxl_u2d2_bridge.ino.
"""

import sys
import time
from dxl_common import (
    open_port, read1, read2, write1, write2,
    ticks_to_deg, deg_to_ticks, is_wheel_mode,
    AX12A_MIN_TICKS, AX12A_MAX_TICKS,
    ADDR_CW_ANGLE_LIMIT, ADDR_CCW_ANGLE_LIMIT,
    ADDR_TORQUE_ENABLE, ADDR_GOAL_POSITION, ADDR_MOVING_SPEED,
    ADDR_PRESENT_POSITION, ADDR_MOVING,
    port_arg
)

DEFAULT_JOINT_CW  = 0
DEFAULT_JOINT_CCW = 1023


def set_joint_mode(pkt, ph, servo_id: int):
    write1(pkt, ph, servo_id, ADDR_TORQUE_ENABLE, 0)
    time.sleep(0.05)
    write2(pkt, ph, servo_id, ADDR_CW_ANGLE_LIMIT,  DEFAULT_JOINT_CW)
    write2(pkt, ph, servo_id, ADDR_CCW_ANGLE_LIMIT, DEFAULT_JOINT_CCW)
    time.sleep(0.1)
    write1(pkt, ph, servo_id, ADDR_TORQUE_ENABLE, 1)


def restore_mode(pkt, ph, servo_id: int, cw: int, ccw: int):
    write1(pkt, ph, servo_id, ADDR_TORQUE_ENABLE, 0)
    time.sleep(0.05)
    write2(pkt, ph, servo_id, ADDR_CW_ANGLE_LIMIT,  cw)
    write2(pkt, ph, servo_id, ADDR_CCW_ANGLE_LIMIT, ccw)
    time.sleep(0.1)


def move_to(pkt, ph, servo_id: int, ticks: int, speed: int):
    ticks = max(AX12A_MIN_TICKS, min(AX12A_MAX_TICKS, ticks))
    write2(pkt, ph, servo_id, ADDR_MOVING_SPEED,  speed)
    write2(pkt, ph, servo_id, ADDR_GOAL_POSITION, ticks)


def wait_for_motion(pkt, ph, servo_id: int, timeout: float = 5.0):
    time.sleep(0.3)
    deadline = time.time() + timeout
    while time.time() < deadline:
        moving = read1(pkt, ph, servo_id, ADDR_MOVING)
        if moving == 0:
            break
        time.sleep(0.02)


def nudge_cycle(pkt, ph, servo_id: int, nudge_deg: float, speed: int,
                saved_cw: int, saved_ccw: int, was_wheel: bool):
    origin = read2(pkt, ph, servo_id, ADDR_PRESENT_POSITION)
    if origin is None:
        print("ERROR: could not read position.", file=sys.stderr)
        return

    origin_deg = ticks_to_deg(origin)
    nudge_pos  = deg_to_ticks(origin_deg + nudge_deg)
    nudge_pos  = max(AX12A_MIN_TICKS, min(AX12A_MAX_TICKS, nudge_pos))

    print(f"Origin  : {origin_deg:.2f} deg (tick {origin})")
    print(f"Nudging : +{nudge_deg} deg...")
    move_to(pkt, ph, servo_id, nudge_pos, speed)
    wait_for_motion(pkt, ph, servo_id)

    after = read2(pkt, ph, servo_id, ADDR_PRESENT_POSITION)
    print(f"Reached : {ticks_to_deg(after or 0):.2f} deg")

    time.sleep(0.5)

    print("Returning to origin...")
    move_to(pkt, ph, servo_id, origin, speed)
    wait_for_motion(pkt, ph, servo_id)

    returned = read2(pkt, ph, servo_id, ADDR_PRESENT_POSITION)
    print(f"Returned: {ticks_to_deg(returned or 0):.2f} deg")

    if was_wheel:
        restore_mode(pkt, ph, servo_id, saved_cw, saved_ccw)
        print("Restored WHEEL mode.")


def main():
    p = port_arg("Nudge an AX-12A servo +N degrees and back")
    p.add_argument("--id",       "-i", type=int,   required=True, help="Servo ID")
    p.add_argument("--nudge",    "-n", type=float,  default=5.0,  help="Degrees to nudge (default: 5)")
    p.add_argument("--speed",    "-s", type=int,    default=200,  help="Goal speed in ticks 1-1023 (default: 200)")
    p.add_argument("--interval", type=float, default=3.0,         help="Seconds between nudge cycles (default: 3)")
    p.add_argument("--once",     action="store_true",              help="Run one cycle then exit")
    args = p.parse_args()

    ph, pkt = open_port(args.port, args.baud)
    servo_id = args.id

    print("==============================================")
    print(f" Dynamixel Servo Nudge (Python / DynamixelSDK)")
    print(f" Port    : {args.port}")
    print(f" Servo ID: {servo_id}  Nudge: +/-{args.nudge} deg")
    print("==============================================")

    model, result, _ = pkt.ping(ph, servo_id)
    if result != 0:
        print(f"ERROR: servo ID {servo_id} not found.", file=sys.stderr)
        ph.closePort()
        sys.exit(1)
    print(f"Servo found (model {model}).")

    # Save current mode
    saved_cw  = read2(pkt, ph, servo_id, ADDR_CW_ANGLE_LIMIT)  or 0
    saved_ccw = read2(pkt, ph, servo_id, ADDR_CCW_ANGLE_LIMIT) or 0
    was_wheel = is_wheel_mode(saved_cw, saved_ccw)

    if was_wheel:
        print("Mode: WHEEL — temporarily switching to JOINT for nudge.")
        set_joint_mode(pkt, ph, servo_id)
        print("Switched to JOINT mode.")
    else:
        print("Mode: JOINT — OK")
        write1(pkt, ph, servo_id, ADDR_TORQUE_ENABLE, 1)

    print("Ready. Starting nudge cycle...\n")

    try:
        while True:
            nudge_cycle(pkt, ph, servo_id, args.nudge, args.speed,
                        saved_cw, saved_ccw, was_wheel)
            if args.once:
                break
            print()
            time.sleep(args.interval)
            if was_wheel:
                set_joint_mode(pkt, ph, servo_id)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ph.closePort()


if __name__ == "__main__":
    main()

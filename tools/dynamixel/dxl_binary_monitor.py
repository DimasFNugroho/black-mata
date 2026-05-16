#!/usr/bin/env python3
"""
dxl_binary_monitor.py — Live monitor for dxl_commander binary STATE frames.

Sends neutral CMD frames at a configurable rate and prints each incoming
STATE frame as human-readable text. The OpenCM must be running
dxl_commander firmware with binary frame support.

This tool does NOT use dxl_common.py (which is text-protocol only).

Usage:
    python tools/dynamixel/dxl_binary_monitor.py --port /dev/ttyACM0
    python tools/dynamixel/dxl_binary_monitor.py --port /dev/ttyACM0 --rate 10
    python tools/dynamixel/dxl_binary_monitor.py --port /dev/ttyACM0 --ids 1 2 3 4

Press Ctrl+C to stop.
"""

import argparse
import glob
import sys
import time

# Allow running from anywhere — add repo root (3 levels up from this file) to path
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))

from software.robot.serial_driver import (
    SerialDriver, ServoCmd, NUM_SERVOS, SERVO_IDS
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def auto_detect_port():
    candidates = (
        glob.glob('/dev/opencm')
        + glob.glob('/dev/serial/by-id/*ROBOTIS*')
        + sorted(glob.glob('/dev/ttyACM*'))
    )
    return candidates[0] if candidates else None


def format_state(state, watch_ids=None):
    """Return a formatted multi-line string for a StateFrame."""
    lines = []
    ts = state.timestamp_ms
    estop = ' *** E-STOP ***' if state.e_stop else ''
    lines.append(f'seq={state.seq:3d}  ts={ts:8d} ms{estop}')
    lines.append(f'  {"ID":<4} {"AVAIL":<6} {"MODE":<6} {"POS(tk)":<8} {"SPEED":<7} {"TEMP":>5} {"VOLT":>6}')
    lines.append(f'  {"-"*50}')
    for s in state.servos:
        if watch_ids and s.servo_id not in watch_ids:
            continue
        if not s.available:
            lines.append(f'  {s.servo_id:<4} {"--":<6}')
            continue
        mode_str = 'WHEEL' if s.mode else 'JOINT'
        lines.append(
            f'  {s.servo_id:<4} {"OK":<6} {mode_str:<6} {s.pos:<8d} {s.speed:<7d}'
            f' {s.temperature:>4d}°C {s.voltage:>5.1f}V'
        )
    return '\n'.join(lines)


def build_neutral_targets():
    """8 neutral targets: steering IDs 1–4 hold centre, wheel IDs 5–8 at zero speed."""
    return (
        [ServoCmd.neutral_joint(pos=512)] * 4 +
        [ServoCmd.stop_wheel()]           * 4
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='dxl_commander binary STATE frame monitor')
    parser.add_argument('--port', '-p', default=None,
                        help='Serial port (default: auto-detect)')
    parser.add_argument('--baud', '-b', type=int, default=115200,
                        help='Baud rate (default: 115200)')
    parser.add_argument('--rate', '-r', type=float, default=5.0,
                        help='CMD frame send rate in Hz (default: 5)')
    parser.add_argument('--ids', type=int, nargs='+', default=None,
                        help='Servo IDs to display (default: all 1–8)')
    args = parser.parse_args()

    port = args.port or auto_detect_port()
    if port is None:
        print('ERROR: No serial port found. Use --port /dev/ttyACM0', file=sys.stderr)
        sys.exit(1)

    watch_ids = set(args.ids) if args.ids else None
    interval  = 1.0 / args.rate

    print(f'Connecting to {port} @ {args.baud} baud...')
    driver = SerialDriver(port, args.baud)
    driver.connect()
    driver.start()

    targets = build_neutral_targets()
    last_state_seq = -1

    print(f'Sending CMD frames at {args.rate} Hz. Press Ctrl+C to stop.\n')

    try:
        while True:
            t0 = time.monotonic()

            driver.send_frame(targets)

            state = driver.get_state()
            if state is not None and state.seq != last_state_seq:
                last_state_seq = state.seq
                # Clear lines and reprint (simple terminal refresh)
                print('\033[2J\033[H', end='')
                print(f'dxl_binary_monitor  |  port={port}  rate={args.rate} Hz')
                print('─' * 56)
                print(format_state(state, watch_ids))
                print('─' * 56)
                print('(Ctrl+C to stop)')

            elapsed = time.monotonic() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        driver.stop()
        driver.close()


if __name__ == '__main__':
    main()

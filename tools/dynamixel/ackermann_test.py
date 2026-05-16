#!/usr/bin/env python3
"""
ackermann_test.py — Interactive Ackermann command tester.

Sends computed servo targets to the real robot and prints the resulting
STATE frame so you can verify steering angles and drive speeds against
physical wheel movement.

Usage:
    python3 tools/dynamixel/ackermann_test.py
    python3 tools/dynamixel/ackermann_test.py --port /dev/ttyACM1

Commands (type at the prompt):
    s <steer_deg> <speed_mps>   e.g.  s 15 0.0   (steer only, no drive)
    s <steer_deg>               e.g.  s 15        (keep current speed)
    f <speed_mps>               e.g.  f 0.2       (go forward, no steer)
    e                           emergency stop
    q                           quit (sends e-stop first)

Tips for physical verification:
  1. Test steer only (speed=0) first — confirm FL/RL turn one way,
     FR/RR the other (counter-phase).
  2. Test drive only (steer=0) — all 4 wheels should spin forward.
  3. Test a gentle turn with speed — outer wheels faster than inner.
"""

import argparse
import glob
import sys
import time

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))

from software.robot.serial_driver import SerialDriver
from software.robot.ackermann import Ackermann, AckermannConfig


def auto_detect_port():
    candidates = (
        glob.glob('/dev/opencm')
        + glob.glob('/dev/serial/by-id/*ROBOTIS*')
        + sorted(glob.glob('/dev/ttyACM*'))
    )
    return candidates[0] if candidates else None


def fmt_steer(cmd, label):
    return f'  {label}: tick={cmd.target:4d}'


def fmt_drive(cmd, label):
    raw = cmd.target
    if raw == 0:
        desc = 'STOP'
    elif raw < 1024:
        desc = f'CCW {raw:4d} ticks'
    else:
        desc = f'CW  {raw-1024:4d} ticks'
    return f'  {label}: {desc}'


def print_targets(targets):
    labels_s = ['FL steer', 'FR steer', 'RL steer', 'RR steer']
    labels_d = ['FL drive', 'FR drive', 'RL drive', 'RR drive']
    print('  -- Steer --')
    for i, lbl in enumerate(labels_s):
        print(fmt_steer(targets[i], lbl))
    print('  -- Drive --')
    for i, lbl in enumerate(labels_d):
        print(fmt_drive(targets[4 + i], lbl))


def print_state(state):
    if state is None:
        print('  (no STATE frame received yet)')
        return
    estop = ' *** E-STOP ***' if state.e_stop else ''
    print(f'  seq={state.seq}  ts={state.timestamp_ms} ms{estop}')
    print(f'  {"ID":<4} {"AVAIL":<6} {"MODE":<6} {"POS":>6} {"SPEED":>6} {"TEMP":>5} {"VOLT":>6}')
    for s in state.servos:
        if not s.available:
            print(f'  {s.servo_id:<4} UNAVAIL')
            continue
        mode = 'WHEEL' if s.mode else 'JOINT'
        print(f'  {s.servo_id:<4} {"OK":<6} {mode:<6} {s.pos:>6d} {s.speed:>6d} {s.temperature:>4d}°C {s.voltage:>5.1f}V')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', default=None)
    parser.add_argument('--baud', '-b', type=int, default=115200)
    args = parser.parse_args()

    port = args.port or auto_detect_port()
    if not port:
        print('ERROR: no serial port found. Use --port /dev/ttyACM0')
        sys.exit(1)

    driver = SerialDriver(port, args.baud)
    driver.connect()
    driver.start()

    ack = Ackermann(AckermannConfig())
    steer = 0.0
    speed = 0.0

    print(f'\nConnected to {port}. Waiting for first STATE frame...')
    time.sleep(0.5)
    print()
    print('Commands:  s <steer_deg> [speed_mps]  |  f <speed_mps>  |  e (estop)  |  q (quit)')
    print()

    try:
        while True:
            try:
                line = input('cmd> ').strip()
            except EOFError:
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            if cmd == 'q':
                break

            elif cmd == 'e':
                driver.send_estop()
                steer, speed = 0.0, 0.0
                print('  E-STOP sent.')

            elif cmd == 's':
                if len(parts) < 2:
                    print('  Usage: s <steer_deg> [speed_mps]')
                    continue
                steer = float(parts[1])
                if len(parts) >= 3:
                    speed = float(parts[2])
                targets = ack.compute(steer, speed)
                driver.send_frame(targets)
                print(f'  → steer={steer}°  speed={speed} m/s')
                print_targets(targets)

            elif cmd == 'f':
                if len(parts) < 2:
                    print('  Usage: f <speed_mps>')
                    continue
                speed = float(parts[1])
                targets = ack.compute(steer, speed)
                driver.send_frame(targets)
                print(f'  → steer={steer}°  speed={speed} m/s')
                print_targets(targets)

            else:
                print('  Unknown command. Try: s 15 0.0 | f 0.2 | e | q')
                continue

            time.sleep(0.1)
            print('  -- STATE --')
            print_state(driver.get_state())
            print()

    except KeyboardInterrupt:
        print()
    finally:
        print('Sending e-stop and disconnecting...')
        driver.send_estop()
        time.sleep(0.1)
        driver.stop()
        driver.close()


if __name__ == '__main__':
    main()

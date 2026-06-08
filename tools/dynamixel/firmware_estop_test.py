#!/usr/bin/env python3
"""
firmware_estop_test.py — SEC 2 firmware watchdog ("e-stop") verification.

WHAT THIS TESTS
    The firmware's autonomous safety: if the Jetson stops sending CMD frames,
    the OpenCM watchdog (WATCHDOG_MS = 500 ms) must drop torque so the robot
    safes itself. This is the firmware-level e-stop. The *latched* dashboard
    e-stop (press → blocked → unlatch) is host-side logic and is covered by the
    system e-stop section, not here.

HOW (safe, nudge-based — no wheel spinning)
    Phase 1  Nudge ONE steering servo ~12° and confirm it moves (command path).
    Phase 2  Cut the CMD-frame stream → after ~0.5 s the watchdog drops torque;
             the servo goes LIMP (commissioner confirms by hand).
    Phase 3  Resume frames → servo re-energises and returns to origin
             (auto-recovery after the watchdog).

    Only the chosen STEERING servo is energised; every other servo is held
    torque-off. Drive (WHEEL) servos are refused — this tool never spins a wheel.

NOTE on the e_stop flag
    The current firmware never reports a fired watchdog in the STATE frame
    (e_stop is always 0 — see the firmware spec), so this test verifies the
    watchdog by SERVO BEHAVIOUR, not by the flag.

RULES
    Run with the agent / dashboard STOPPED (this tool drives the port and resets
    the board on connect). Raise the robot or clear the wheels before running.

Usage:
    python tools/dynamixel/firmware_estop_test.py
    python tools/dynamixel/firmware_estop_test.py --id 2          # pick a steer servo
    python tools/dynamixel/firmware_estop_test.py --port /dev/opencm

Ctrl+C aborts (torque is released on exit).
"""

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tools/dynamixel

from software.robot.serial_driver import SerialDriver, ServoCmd, SERVO_IDS
from dxl_binary_monitor import auto_detect_port, load_config, port_is_busy

NUDGE_TICKS  = 40      # ~11.7° on an AX-12A (300°/1023 ticks)
RATE_HZ      = 5.0     # frame rate while driving (under the 500 ms watchdog)
SILENCE_S    = 1.2     # comms-cut window (> WATCHDOG_MS = 500 ms)
MODE_JOINT   = 0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def build_targets(chosen_id, goal, enable, id_mode):
    """Per-ID targets: only `chosen_id` is energised (JOINT, to `goal`); every
    other servo is torque-off in its configured mode. Send with servo_ids=SERVO_IDS."""
    out = []
    for sid in SERVO_IDS:
        if sid == chosen_id:
            out.append(ServoCmd(mode=MODE_JOINT, enable_torque=1 if enable else 0, target=goal))
        else:
            out.append(ServoCmd(mode=id_mode.get(sid, 0), enable_torque=0, target=0))
    return out


def drive_for(driver, targets, seconds):
    """Send `targets` at RATE_HZ for `seconds`; return the latest STATE seen."""
    interval = 1.0 / RATE_HZ
    end  = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        t0 = time.monotonic()
        driver.send_frame(targets, servo_ids=SERVO_IDS)
        st = driver.get_state()
        if st is not None:
            last = st
        s = interval - (time.monotonic() - t0)
        if s > 0:
            time.sleep(s)
    return last


def pos_of(state, servo_id):
    if state is None:
        return None
    sv = state.servos[servo_id - 1]
    return sv.pos if sv.available else None


def main():
    parser = argparse.ArgumentParser(description='Firmware watchdog (e-stop) test — nudge based')
    parser.add_argument('--port', '-p', default=None, help='Serial port (default: auto-detect)')
    parser.add_argument('--baud', '-b', type=int, default=115200)
    parser.add_argument('--config', '-c', default=None, help='ackermann_config.json path')
    parser.add_argument('--id', type=int, default=None,
                        help='Steering servo ID to nudge (default: first steering servo)')
    parser.add_argument('--force', action='store_true', help='Connect even if the port is busy')
    args = parser.parse_args()

    config_path = args.config or str(REPO_ROOT / 'tools' / 'ackermann_ui' / 'ackermann_config.json')
    try:
        servo_ids, id_role, id_mode = load_config(config_path)
    except Exception as e:
        print('ERROR loading config {}: {}'.format(config_path, e), file=sys.stderr)
        return 1

    # Choose a STEERING servo (mode JOINT). Refuse a drive/WHEEL servo.
    steer_ids = [sid for sid in servo_ids if id_mode[sid] == MODE_JOINT]
    chosen = args.id if args.id is not None else steer_ids[0]
    if chosen not in id_mode:
        print('ERROR: ID {} is not in the config.'.format(chosen), file=sys.stderr)
        return 1
    if id_mode[chosen] != MODE_JOINT:
        print('ERROR: ID {} is a DRIVE/WHEEL servo ({}). This test only nudges a '
              'STEERING servo for safety. Pick one of: {}'
              .format(chosen, id_role[chosen], steer_ids), file=sys.stderr)
        return 1

    port = args.port or auto_detect_port()
    if port is None:
        print('ERROR: no serial port found. Use --port /dev/opencm', file=sys.stderr)
        return 1
    if port_is_busy(port) and not args.force:
        print('ERROR: {} is in use — stop the dashboard/agent first '
              '(sudo systemctl stop black-mata-dashboard).'.format(port), file=sys.stderr)
        return 2

    print('Firmware watchdog (e-stop) test')
    print('  servo : ID {} ({})'.format(chosen, id_role[chosen]))
    print('  port  : {}'.format(port))
    print('\nSAFETY: this nudges ONE steering servo ~12°. Raise the robot or clear the')
    print('        wheels so nothing is obstructed. Every other servo stays torque-off.')
    try:
        input('Press Enter to begin (Ctrl+C to abort)... ')
    except KeyboardInterrupt:
        print('\nAborted.'); return 130

    driver = SerialDriver(port, args.baud)
    driver.connect()
    driver.start()

    results = {}
    origin = None
    try:
        # Read origin position (all torque off).
        off_targets = build_targets(chosen, 0, False, id_mode)
        st = drive_for(driver, off_targets, 0.8)
        origin = pos_of(st, chosen)
        if origin is None:
            print('ERROR: servo ID {} not available — cannot read position.'.format(chosen),
                  file=sys.stderr)
            return 1
        print('\nOrigin position: {} ticks'.format(origin))

        # Pick a nudge direction that stays in range.
        goal = clamp(origin + NUDGE_TICKS, 0, 1023)
        if goal == origin:
            goal = clamp(origin - NUDGE_TICKS, 0, 1023)

        # ── Phase 1 — command path: nudge and confirm motion ───────────────────
        print('\n[Phase 1] Nudging ID {} from {} → {} ...'.format(chosen, origin, goal))
        st = drive_for(driver, build_targets(chosen, goal, True, id_mode), 1.6)
        after = pos_of(st, chosen)
        moved = abs((after or origin) - origin)
        results['Phase 1 — servo responds to command'] = (moved >= NUDGE_TICKS * 0.5,
                                                           'moved {} ticks (pos {})'.format(moved, after))
        print('  moved {} ticks (now at {})'.format(moved, after))

        # ── Phase 2 — watchdog drops torque on comms loss ──────────────────────
        drive_for(driver, build_targets(chosen, goal, True, id_mode), 0.4)   # settle, holding
        print('\n[Phase 2] Cutting CMD frames for {:.1f} s — watchdog should drop torque...'.format(SILENCE_S))
        time.sleep(SILENCE_S)     # NO frames → watchdog fires at ~0.5 s
        print('  Comms are cut and torque should be OFF now.')
        try:
            ans = input('  Is the servo LIMP — free to move by hand, no holding force? [y/N] ')
        except KeyboardInterrupt:
            raise
        results['Phase 2 — watchdog releases torque'] = (ans.strip().lower().startswith('y'),
                                                         'commissioner observation')

        # ── Phase 3 — auto-recovery after the watchdog ─────────────────────────
        print('\n[Phase 3] Resuming frames → servo should re-energise and return to {} ...'.format(origin))
        st = drive_for(driver, build_targets(chosen, origin, True, id_mode), 1.6)
        back = pos_of(st, chosen)
        returned = abs((back if back is not None else origin) - origin)
        results['Phase 3 — recovers and is controllable'] = (returned <= NUDGE_TICKS * 0.5,
                                                             'returned to {} (Δ{})'.format(back, returned))
        print('  back at {} (Δ{} from origin)'.format(back, returned))

    except KeyboardInterrupt:
        print('\nAborted by user.')
    finally:
        # Always release torque and return toward origin before disconnecting.
        if origin is not None:
            drive_for(driver, build_targets(chosen, origin, False, id_mode), 0.4)
        driver.stop()
        driver.close()

    # ── Verdict ────────────────────────────────────────────────────────────────
    print('\n── Firmware watchdog (e-stop) result ' + '─' * 24)
    ok = True
    for name, (passed, detail) in results.items():
        ok = ok and passed
        print('  [{}] {:<38} {}'.format('PASS' if passed else 'FAIL', name, detail))
    print('─' * 60)
    print('RESULT: {}'.format('PASS' if (ok and len(results) == 3) else 'FAIL'))
    return 0 if (ok and len(results) == 3) else 1


if __name__ == '__main__':
    sys.exit(main())

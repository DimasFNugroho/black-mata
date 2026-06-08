#!/usr/bin/env python3
"""
dxl_binary_monitor.py — READ-ONLY state monitor for dxl_commander STATE frames.

ROLE OF THIS TOOL
    A passive inspector of the OpenCM's binary STATE telemetry. Its job is to
    confirm the firmware reports every reading the black-mata system needs to
    operate — per-servo {available, mode, position, speed, temperature, voltage}
    plus the global {seq, timestamp, e_stop} — at a healthy rate and with sane
    values. It is the instrument for SEC 2 (Firmware Verification).

    It deliberately does NOT drive the robot or change its configuration. This
    tool is about DATA READING only; verifying the command path (set position /
    speed / mode / torque / e-stop) is an active test that belongs to the
    ackermann_ui / dashboard bring-up, not here.

PROTOCOL CONSTRAINT (why a "read" still transmits)
    dxl_commander is request/response — it emits a STATE frame ONLY in reply to
    a CMD frame, so this monitor must send something to get a reading. It sends
    a no-op CMD frame: torque OFF for every servo, carrying each servo's current
    mode. Torque off ⇒ no motion; matching the existing mode ⇒ no EEPROM
    angle-limit rewrite / mode thrash. (A truly zero-side-effect read would need
    a dedicated "poll" frame type in the firmware.)

    Servos go LIMP while this runs (torque off) — that is the safe read-only
    behaviour, not a fault.

OPERATING RULES
    • Run with the agent / dashboard STOPPED. Two writers on one serial port
      corrupt the stream and fight over servo modes. The tool refuses to start
      if the port is already in use (override with --force).
    • Connecting resets the OpenCM (DTR), so do not launch this mid-operation.
    • Loads servo IDs and roles from ackermann_config.json (ackermann_ui output)
      so labels and seed modes match the real robot.

Usage:
    python tools/dynamixel/dxl_binary_monitor.py
    python tools/dynamixel/dxl_binary_monitor.py --port /dev/opencm --rate 5
    python tools/dynamixel/dxl_binary_monitor.py --config path/to/ackermann_config.json

Press Ctrl+C to stop.
"""

import argparse
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

# Allow running from anywhere — add repo root (3 levels up from this file) to path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from software.robot.serial_driver import (
    SerialDriver, ServoCmd, NUM_SERVOS, SERVO_IDS
)

DEFAULT_CONFIG = REPO_ROOT / 'tools' / 'ackermann_ui' / 'ackermann_config.json'

# ackermann_config.json stores servo_ids in role order: first four steering,
# last four drive. Steering servos run in JOINT (mode 0), drive in WHEEL (mode 1).
ROLE_NAMES = ['FL_steer', 'FR_steer', 'RL_steer', 'RR_steer',
              'FL_drive', 'FR_drive', 'RL_drive', 'RR_drive']
ROLE_MODE  = [0, 0, 0, 0, 1, 1, 1, 1]
MODE_NAME  = {0: 'JOINT', 1: 'WHEEL'}

MIN_SAFE_RATE_HZ = 3.0   # below this the firmware watchdog (500 ms) may trip

# Sanity ranges for the black-mata STATE contract (AX-12A on a 3S/12 V system).
POS_MAX            = 1023     # AX-12A 10-bit present position
SPEED_MAX          = 2047     # AX-12A present-speed encoding
TEMP_MAX_C         = 80       # °C — above this is implausible / past shutdown
VOLT_MIN, VOLT_MAX = 9.5, 13.5


# ── Helpers ────────────────────────────────────────────────────────────────────

def auto_detect_port():
    candidates = (
        glob.glob('/dev/opencm')
        + glob.glob('/dev/serial/by-id/*ROBOTIS*')
        + sorted(glob.glob('/dev/ttyACM*'))
    )
    return candidates[0] if candidates else None


def load_config(path):
    """Load ackermann_config.json → (servo_ids, id_role, id_mode).

    id_role / id_mode are keyed by PHYSICAL servo ID, derived from the role
    order of servo_ids. Raises on a missing / malformed config so the monitor
    never silently falls back to wrong assumptions.
    """
    with open(path) as f:
        cfg = json.load(f)
    servo_ids = cfg.get('servo_ids')
    if not isinstance(servo_ids, list) or len(servo_ids) != NUM_SERVOS:
        raise ValueError(
            'config "servo_ids" must list {} IDs in role order; got {!r}'
            .format(NUM_SERVOS, servo_ids))
    id_role, id_mode = {}, {}
    for role_idx, sid in enumerate(servo_ids):
        id_role[sid] = ROLE_NAMES[role_idx]
        id_mode[sid] = ROLE_MODE[role_idx]
    return servo_ids, id_role, id_mode


def port_is_busy(port):
    """True if another process already holds the port (e.g. the dashboard agent).

    Uses fuser; if fuser is unavailable we cannot check and assume free.
    """
    try:
        r = subprocess.run(['fuser', port],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def seed_targets(id_mode):
    """Frame-0 read-only targets (torque OFF), seeded with each servo's CONFIGURED
    mode so the very first frame is unlikely to trigger a mode switch.
    Ordered by physical ID; sent with servo_ids = SERVO_IDS (1..8)."""
    return [ServoCmd(mode=id_mode.get(sid, 0), enable_torque=0, target=0)
            for sid in SERVO_IDS]


def echo_targets(state):
    """Read-only targets (torque OFF) that MIRROR each servo's currently reported
    mode — so we never change configuration after the first reading. Ordered by
    physical ID to match state.servos."""
    return [ServoCmd(mode=s.mode, enable_torque=0, target=0)
            for s in state.servos]


def format_state(state, id_role):
    """Human-readable rendering of one STATE frame."""
    lines = []
    estop = '  *** E-STOP LATCHED ***' if state.e_stop else '  e_stop=0 (normal)'
    lines.append('seq={:3d}  ts={:>10d} ms{}'.format(state.seq, state.timestamp_ms, estop))
    lines.append('  {:<3} {:<9} {:<6} {:<6} {:<8} {:<7} {:>5} {:>6}'.format(
        'ID', 'ROLE', 'AVAIL', 'MODE', 'POS(tk)', 'SPEED', 'TEMP', 'VOLT'))
    lines.append('  ' + '-' * 62)
    for s in state.servos:
        role = id_role.get(s.servo_id, '?')
        if not s.available:
            lines.append('  {:<3} {:<9} {:<6}'.format(s.servo_id, role, '--'))
            continue
        mode = MODE_NAME.get(s.mode, '?({})'.format(s.mode))
        lines.append('  {:<3} {:<9} {:<6} {:<6} {:<8d} {:<7d} {:>3d}°C {:>5.1f}V'.format(
            s.servo_id, role, 'OK', mode, s.pos, s.speed, s.temperature, s.voltage))
    lines.append('  ' + '-' * 62)
    # Coverage note: the STATE frame reserves 64 bytes for IMU which the current
    # firmware leaves zeroed — surface it so SEC 2 can flag the gap vs the spec.
    lines.append('  IMU block: reserved (zeros) — not yet provided by firmware')
    return '\n'.join(lines)


# ── Contract check (SEC 2 heartbeat + data) ────────────────────────────────────

def run_check(driver, id_role, id_mode, rate, seconds):
    """Run read-only for `seconds` and assert the black-mata STATE contract:
    seq advancing with no gaps, every configured servo available each frame,
    pos/speed/temp/voltage in range, mode matching config. Returns 0 on PASS,
    1 on FAIL. Does NOT move any servo (torque stays off)."""
    interval = 1.0 / rate
    targets  = seed_targets(id_mode)

    frames = 0
    seq_gaps = 0
    last_seq = None
    unavailable = set()                 # servo IDs ever reporting available=0
    out_of_range = []                   # (id, field, value)
    mode_mismatch = set()               # servo IDs whose mode != config
    estop_set = False
    vmin, vmax = None, None             # voltage span observed

    print('Running contract check for {:.0f} s (read-only)...'.format(seconds))
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        driver.send_frame(targets, servo_ids=SERVO_IDS)
        state = driver.get_state()
        if state is not None:
            targets = echo_targets(state)
            if state.seq != last_seq:
                if last_seq is not None:
                    step = (state.seq - last_seq) % 256   # uint8 rollover-safe
                    if step > 1:
                        seq_gaps += step - 1
                last_seq = state.seq
                frames += 1
                if state.e_stop:
                    estop_set = True
                for sv in state.servos:
                    if sv.servo_id not in id_role:
                        continue
                    if not sv.available:
                        unavailable.add(sv.servo_id)
                        continue
                    if not (0 <= sv.pos <= POS_MAX):
                        out_of_range.append((sv.servo_id, 'pos', sv.pos))
                    if not (0 <= sv.speed <= SPEED_MAX):
                        out_of_range.append((sv.servo_id, 'speed', sv.speed))
                    if not (0 < sv.temperature <= TEMP_MAX_C):
                        out_of_range.append((sv.servo_id, 'temp', sv.temperature))
                    if not (VOLT_MIN <= sv.voltage <= VOLT_MAX):
                        out_of_range.append((sv.servo_id, 'volt', round(sv.voltage, 1)))
                    if sv.mode != id_mode[sv.servo_id]:
                        mode_mismatch.add(sv.servo_id)
                    vmin = sv.voltage if vmin is None else min(vmin, sv.voltage)
                    vmax = sv.voltage if vmax is None else max(vmax, sv.voltage)
        sleep_for = interval - (time.monotonic() - t0)
        if sleep_for > 0:
            time.sleep(sleep_for)

    # ── Verdict ──────────────────────────────────────────────────────────────
    expected = rate * seconds
    checks = []
    checks.append(('STATE frames received',
                   frames >= 0.5 * expected,
                   '{} frames (~{:.0f} expected at {} Hz)'.format(frames, expected, rate)))
    checks.append(('seq advances, no gaps',
                   frames > 0 and seq_gaps == 0,
                   '{} gap(s)'.format(seq_gaps)))
    checks.append(('all configured servos available',
                   not unavailable,
                   'missing: {}'.format(sorted(unavailable)) if unavailable else 'all 8 OK'))
    checks.append(('pos/speed/temp/voltage in range',
                   not out_of_range,
                   '{} reading(s) out of range'.format(len(out_of_range)) if out_of_range else 'all in range'))
    checks.append(('servo modes match config',
                   not mode_mismatch,
                   'mismatch: {}'.format(sorted(mode_mismatch)) if mode_mismatch else 'all match'))
    checks.append(('e_stop reported normal',
                   not estop_set,
                   'e_stop=1 seen' if estop_set else 'e_stop=0 throughout'))

    print('\n── black-mata STATE contract ' + '─' * 32)
    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        print('  [{}] {:<34} {}'.format('PASS' if passed else 'FAIL', name, detail))
    if vmin is not None:
        print('  voltage span observed: {:.1f}–{:.1f} V'.format(vmin, vmax))
    if out_of_range:
        for sid, field, val in out_of_range[:8]:
            print('     out-of-range: ID{} {}={}'.format(sid, field, val))
    print('─' * 60)
    print('RESULT: {}'.format('PASS' if ok else 'FAIL'))
    print('Note: e_stop is informational only — current firmware never reports a '
          'fired watchdog (see firmware spec). Verify the watchdog with the e-stop test.')
    return 0 if ok else 1


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='dxl_commander READ-ONLY binary STATE monitor (no servo motion)')
    parser.add_argument('--port', '-p', default=None,
                        help='Serial port (default: auto-detect)')
    parser.add_argument('--baud', '-b', type=int, default=115200,
                        help='Baud rate (default: 115200)')
    parser.add_argument('--rate', '-r', type=float, default=5.0,
                        help='Poll rate in Hz (default: 5; keep >= 3 to avoid the watchdog)')
    parser.add_argument('--config', '-c', default=str(DEFAULT_CONFIG),
                        help='ackermann_config.json path (default: tools/ackermann_ui/...)')
    parser.add_argument('--force', action='store_true',
                        help='Connect even if the port appears busy (NOT recommended)')
    parser.add_argument('--check', type=float, metavar='SECONDS', default=None,
                        help='Run the contract check for SECONDS and exit PASS/FAIL (read-only)')
    args = parser.parse_args()

    # 1) Load the config FIRST — it defines servo IDs, roles, and seed modes.
    try:
        servo_ids, id_role, id_mode = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print('ERROR loading config {}: {}'.format(args.config, e), file=sys.stderr)
        sys.exit(1)

    port = args.port or auto_detect_port()
    if port is None:
        print('ERROR: no serial port found. Use --port /dev/opencm', file=sys.stderr)
        sys.exit(1)

    # 2) Refuse to fight the agent for the port (the cause of mode-flipping / garbage).
    if port_is_busy(port) and not args.force:
        print(
            'ERROR: {} is already in use — the dashboard/agent is probably running.\n'
            '       Stop it first:  sudo systemctl stop black-mata-dashboard\n'
            '       Two writers on one port corrupt the stream and flip servo modes.\n'
            '       Override with --force only if you are sure the port is free.'
            .format(port), file=sys.stderr)
        sys.exit(2)

    if args.rate < MIN_SAFE_RATE_HZ:
        print('WARNING: rate {} Hz is below {} Hz — the firmware watchdog (500 ms) '
              'may trip and set e_stop.'.format(args.rate, MIN_SAFE_RATE_HZ),
              file=sys.stderr)

    interval = 1.0 / args.rate
    print('Config : {}'.format(args.config))
    print('Roles  : ' + ', '.join('{}:{}'.format(sid, id_role[sid]) for sid in servo_ids))
    print('Connecting to {} @ {} baud  —  READ-ONLY (torque off, no motion)'.format(port, args.baud))

    driver = SerialDriver(port, args.baud)
    driver.connect()
    driver.start()

    targets  = seed_targets(id_mode)   # frame 0: configured modes, torque off
    last_seq = -1

    try:
        # ── Contract-check mode: assert + exit, no live display ──────────────────
        if args.check is not None:
            rc = run_check(driver, id_role, id_mode, args.rate, args.check)
            return rc

        print('Polling at {} Hz. Press Ctrl+C to stop.\n'.format(args.rate))
        while True:
            t0 = time.monotonic()

            # Send by physical ID (slot i -> ID i+1). Torque is always off here.
            driver.send_frame(targets, servo_ids=SERVO_IDS)

            state = driver.get_state()
            if state is not None:
                # Stay passive: mirror the modes the firmware currently reports,
                # so subsequent frames never change servo configuration.
                targets = echo_targets(state)

                if state.seq != last_seq:
                    last_seq = state.seq
                    print('\033[2J\033[H', end='')           # clear + home
                    print('dxl_binary_monitor (READ-ONLY)  |  port={}  rate={} Hz'.format(port, args.rate))
                    print('─' * 64)
                    print(format_state(state, id_role))
                    print('─' * 64)
                    print('(torque off — servos limp, no motion · Ctrl+C to stop)')

            sleep_for = interval - (time.monotonic() - t0)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        driver.stop()
        driver.close()


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
gamepad_test.py — Read-only gamepad input validator for Black-Mata.

Shows every axis (raw + normalised) and button. Normalization uses a
per-controller calibration file so both stick extremes reach exactly ±1.0.

Run:
    python3 tools/gamepad/gamepad_test.py
    python3 tools/gamepad/gamepad_test.py --device /dev/input/event10
    python3 tools/gamepad/gamepad_test.py --list
    python3 tools/gamepad/gamepad_test.py --calibrate          # create/update calib file

Quit with q or Ctrl-C.

Drive mapping (edit MAP dict to remap):
    Left stick X         -> steer      (-1 .. +1)
    Left stick Y         -> throttle   (+1 fwd .. -1 rev)
    L1 held              -> arm (dead-man)
    L1 + D-pad diagonal  -> e-stop     (latched, TL held + HAT0X≠0 + HAT0Y≠0)
    LS + RS (click both) -> re-arm after e-stop
"""

import argparse
import curses
import json
import select
import sys
import time
from pathlib import Path

# Reusable primitives shared with tools/dashboard/server.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gamepad_core import (    # noqa: E402
    evdev,
    DEADZONE,
    MAP,
    GamepadState,
    list_devices,
    find_gamepad,
    dev_path,
    load_calibration,
    save_calibration,
    normalize_stick,
    normalize_trigger,
    _abs_name,
    _btn_name,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'ackermann_ui' / 'ackermann_config.json'

REFRESH_HZ = 30.0


# ── Calibration procedure (interactive, no curses) ───────────────────────────

def run_calibration(dev):
    print()
    print('── Calibration: {} ──'.format(dev.name))
    print()
    print('  ── G3 V2 hardware calibration (do this first) ──────────────────')
    print('  The controller must be calibrated in firmware before this script')
    print('  can capture meaningful axis ranges. Skip if already done.')
    print()
    print('    1. Press Home + Select + B simultaneously once.')
    print('       The controller enters calibration mode — LEDs will blink blue.')
    print('    2. Slowly move ALL analog sticks and triggers to their')
    print('       full extents in every direction.')
    print('    3. Press Start to confirm and save calibration on the controller.')
    print()
    print('  After the controller confirms, continue below.')
    print('  ────────────────────────────────────────────────────────────────')
    print()
    input('  Press Enter when the controller hardware calibration is done...')
    print()

    abs_pairs = dev.capabilities(absinfo=True).get(evdev.ecodes.EV_ABS, [])
    abs_info  = {code: info for code, info in abs_pairs}

    # ── Step 1: capture resting center ───────────────────────────────────────
    print('Step 1/2 — Rest position')
    print('  1. Wiggle ALL sticks and triggers a little, then release them.')
    print('  2. Let go completely and keep hands off.')
    print('  3. Press Enter — we will sample for 2 seconds to capture the rest position.')
    input('  Press Enter when ready...')
    print()
    print('  Sampling rest position for 2 seconds — keep hands off sticks...')

    # absinfo.value is stale until events are read; sample live events instead.
    centers = {}
    t_end = time.monotonic() + 2.0
    while time.monotonic() < t_end:
        remaining = t_end - time.monotonic()
        readable, _, _ = select.select([dev.fd], [], [], 0.05)
        if readable:
            try:
                for ev in dev.read():
                    if ev.type == evdev.ecodes.EV_ABS and ev.code in abs_info:
                        centers[ev.code] = ev.value
            except (BlockingIOError, OSError):
                pass
        print('  {:3.1f}s remaining...  '.format(remaining), end='\r', flush=True)
    print()

    # Fallback for axes that sent no events (truly stable at hardware default).
    for code, info in abs_info.items():
        if code not in centers:
            centers[code] = dev.absinfo(code).value

    print('  Captured centers:')
    for code in sorted(centers):
        print('    {:<14s}: {:6d}'.format(_abs_name(code), centers[code]))

    # ── Step 2: sweep extremes ────────────────────────────────────────────────
    print()
    print('Step 2/2 — Full range sweep  (10 seconds)')
    print('  Slowly move every stick to all four corners.')
    print('  Press every trigger fully. Press Enter to start...')
    input()

    lo_seen = dict(centers)
    hi_seen = dict(centers)

    t_start = time.monotonic()
    SWEEP_SECS = 10.0
    while True:
        elapsed   = time.monotonic() - t_start
        remaining = SWEEP_SECS - elapsed
        if remaining <= 0:
            break

        readable, _, _ = select.select([dev.fd], [], [], 0.05)
        if readable:
            try:
                for ev in dev.read():
                    if ev.type == evdev.ecodes.EV_ABS and ev.code in lo_seen:
                        lo_seen[ev.code] = min(lo_seen[ev.code], ev.value)
                        hi_seen[ev.code] = max(hi_seen[ev.code], ev.value)
            except (BlockingIOError, OSError):
                pass

        print('  {:4.1f}s remaining   '.format(remaining), end='\r', flush=True)

    print()
    print()

    # ── Build and save ────────────────────────────────────────────────────────
    axes_calib = {}
    print('  Axis ranges captured:')
    for code in sorted(abs_info):
        info   = abs_info[code]
        lo     = lo_seen[code]
        hi     = hi_seen[code]
        center = centers[code]
        axes_calib[code] = {
            'name':   _abs_name(code),
            'lo':     lo,
            'hi':     hi,
            'center': center,
        }
        print('    {:<14s}: lo={:6d}  hi={:6d}  center={:6d}'.format(
            _abs_name(code), lo, hi, center))

    path = save_calibration(dev.name, axes_calib)
    print()
    print('  Saved: {}'.format(path))
    print()
    print('  Re-run without --calibrate to use it.')
    print()


# ── Config ────────────────────────────────────────────────────────────────────

def load_max_steer_deg():
    try:
        return float(json.loads(CONFIG_PATH.read_text()).get('max_steer_deg', 30.0))
    except Exception:
        return 30.0


# ── Rendering helpers ─────────────────────────────────────────────────────────

def _bipolar_bar(value, half_width=10):
    value = max(-1.0, min(1.0, value))
    cells = int(round(abs(value) * half_width))
    if value < 0:
        left  = ' ' * (half_width - cells) + '#' * cells
        right = ' ' * half_width
    else:
        left  = ' ' * half_width
        right = '#' * cells + ' ' * (half_width - cells)
    return '[' + left + '|' + right + ']'


def _unipolar_bar(value, width=20):
    value = max(0.0, min(1.0, value))
    cells = int(round(value * width))
    return '[' + '#' * cells + ' ' * (width - cells) + ']'


def _safe_addstr(stdscr, y, x, s, attr=0):
    try:
        stdscr.addstr(y, x, s, attr)
    except curses.error:
        pass


def _render(stdscr, dev, state, max_steer_deg, has_calib):
    max_y, max_x = stdscr.getmaxyx()

    steer    = state.steer_norm()
    throttle = state.throttle_norm()
    arm      = state.button(MAP['arm_btn'])
    latched  = state.estop_latched
    combo    = state._estop_combo_active()
    armed    = arm and not latched

    y = 0
    calib_note = 'calibrated' if has_calib else 'NO CALIB — run --calibrate'
    _safe_addstr(stdscr, y, 0,
        'Black-Mata Gamepad Test    q=quit  [{}]'.format(calib_note),
        curses.A_BOLD);                                                                   y += 1
    _safe_addstr(stdscr, y, 0, 'Device : {}  ({})'.format(dev.name, dev_path(dev)));      y += 1
    _safe_addstr(stdscr, y, 0, 'Events : {:4d}/s    last: {:.3f} s ago'.format(
        state.events_per_sec(), state.age_since_last()));                                 y += 2

    # ── All axes ──────────────────────────────────────────────────────────────
    _safe_addstr(stdscr, y, 0,
        '── AXES ({}) ── {:>14s} {:>7s}  bar'.format(
            len(state.abs_info), 'raw', 'norm'),
        curses.A_BOLD);                                                                   y += 1

    for code, info in sorted(state.abs_info.items()):
        if y >= max_y - 1:
            break
        lo, hi, center = state._cal(code)
        raw  = state.axes.get(code, center)
        name = _abs_name(code)
        role = ''
        if   code == MAP['steer_axis']:    role = '(steer)'
        elif code == MAP['throttle_axis']: role = '(throttle)'

        val = state.axis_norm(code)
        if center > lo:
            bar = _bipolar_bar(val, 10)
            _safe_addstr(stdscr, y, 0,
                '  {:<12s}{:<9s} {:6d}  {:+.2f}  {}'.format(name, role, raw, val, bar))
        else:
            bar = _unipolar_bar(val, 20)
            _safe_addstr(stdscr, y, 0,
                '  {:<12s}{:<9s} {:6d}   {:.2f}  {}'.format(name, role, raw, val, bar))
        y += 1
    y += 1

    # ── All buttons ───────────────────────────────────────────────────────────
    if y < max_y - 1:
        _safe_addstr(stdscr, y, 0,
            '── BUTTONS ({}) ──'.format(len(state.btn_codes)),
            curses.A_BOLD);                                                               y += 1

    BTN_W   = 8
    BTN_COL = max(1, (max_x - 2) // BTN_W)

    for i, code in enumerate(state.btn_codes):
        if y >= max_y - 1:
            break
        col = i % BTN_COL
        if col == 0 and i != 0:
            y += 1
        if y >= max_y - 1:
            break
        name    = _btn_name(code)[:6]
        pressed = state.buttons.get(code, False)
        label   = '[{:<6s}]'.format(name)
        attr    = curses.A_BOLD | curses.A_REVERSE if pressed else curses.A_DIM
        _safe_addstr(stdscr, y, 2 + col * BTN_W, label, attr)

    y += 2

    # ── Drive preview ─────────────────────────────────────────────────────────
    if y < max_y - 1:
        _safe_addstr(stdscr, y, 0,
            '── DRIVE PREVIEW (not sent to robot) ──', curses.A_BOLD);                   y += 1
    if combo:
        status = 'E-STOPPING — L1 + D-pad diagonal active'
    elif latched:
        status = 'E-STOPPED  — click both thumbsticks to re-arm'
    elif armed:
        status = 'ARMED      — drive frames would flow'
    else:
        status = 'DISARMED   — hold L1 to arm'

    direction = 'fwd' if throttle >= 0 else 'rev'
    for line in [
        'Status   : {}'.format(status),
        'Steer    : {:+6.2f}°  (max ±{:.0f}°)'.format(steer * max_steer_deg, max_steer_deg),
        'Throttle : {:+5.2f}  ({})'.format(throttle, direction),
    ]:
        if y >= max_y - 1:
            break
        _safe_addstr(stdscr, y, 0, '  ' + line);                                         y += 1


def run_ui(stdscr, dev, state, max_steer_deg, has_calib):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()

    fd     = dev.fd
    period = 1.0 / REFRESH_HZ

    while True:
        readable, _, _ = select.select([fd], [], [], 0.0)
        if readable:
            try:
                for ev in dev.read():
                    state.feed(ev)
            except BlockingIOError:
                pass
            except OSError as e:
                stdscr.erase()
                _safe_addstr(stdscr, 0, 0,
                    'Device disconnected: {} ({})'.format(dev_path(dev), e))
                stdscr.refresh()
                time.sleep(2)
                return

        stdscr.erase()
        _render(stdscr, dev, state, max_steer_deg, has_calib)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord('q'), ord('Q')):
            return

        time.sleep(period)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Gamepad input validator for Black-Mata (read-only).'
    )
    ap.add_argument('--device',    '-d', default=None,
                    help='Path to /dev/input/eventN (auto-detected if omitted)')
    ap.add_argument('--list',      '-l', action='store_true',
                    help='List all input devices and exit')
    ap.add_argument('--calibrate', '-C', action='store_true',
                    help='Run calibration procedure and save calib file')
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    if args.device:
        dev = evdev.InputDevice(args.device)
    else:
        dev = find_gamepad()
        if dev is None:
            sys.stderr.write(
                'No gamepad detected.\n'
                '  Try:  python3 tools/gamepad/gamepad_test.py --list\n'
                '  Then: python3 tools/gamepad/gamepad_test.py --device /dev/input/eventN\n'
                'Pair the controller first with: bash tools/gamepad/setup_gamepad.sh\n'
            )
            sys.exit(1)

    print('Using device: {} ({})'.format(dev.name, dev_path(dev)))

    if args.calibrate:
        run_calibration(dev)
        dev.close()
        return

    calib     = load_calibration(dev.name)
    has_calib = calib is not None
    if not has_calib:
        print('No calibration file found for "{}".'.format(dev.name))
        print()
        print('  ── G3 V2 hardware calibration (do this first, once) ────────────────')
        print('  1. Press Home + Select + B simultaneously once.')
        print('     The controller enters calibration mode — LEDs will blink blue.')
        print('  2. Slowly move ALL analog sticks and triggers to their full extents.')
        print('  3. Press Start to confirm and save calibration on the controller.')
        print('  ────────────────────────────────────────────────────────────────────')
        print()
        print('  Then run:  python3 tools/gamepad/gamepad_test.py --calibrate')
        print('  to record axis ranges into a calibration file.')
        print()
        input('  Press Enter to continue without calibration...')
        print()

    state         = GamepadState(dev, calib)
    max_steer_deg = load_max_steer_deg()

    try:
        curses.wrapper(run_ui, dev, state, max_steer_deg, has_calib)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            dev.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()

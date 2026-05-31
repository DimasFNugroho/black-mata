"""
gamepad_core.py — Reusable gamepad reading and normalisation primitives.

Shared by:
  - tools/gamepad/gamepad_test.py     (interactive curses validator)
  - tools/dashboard/server.py         (live drive integration)

Anything that is curses- or terminal-specific stays in gamepad_test.py.

evdev dependency (authoritative note — other files reference this one):
  Requires evdev >= 0.7.0. The code is version-agnostic: dev_path() abstracts
  the only API difference (`.fn` in 0.7.0 vs `.path` in >= 1.0), and codes that
  may be absent on a given build are looked up with getattr(..., None). So any
  build at or above the floor behaves identically — there is no version pin.
    - Jetson (real target, Python 3.6): apt's python3-evdev, which is 0.7.0.
    - x86 (eval/test only): pip's current build. Apt installs to system
      site-packages and is invisible inside a venv, so use pip there.
"""

import json
import re
import sys
import time
from pathlib import Path

try:
    import evdev
except ImportError:
    sys.stderr.write(
        "evdev not installed.\n"
        "  sudo apt install python3-evdev      (Jetson / system Python)\n"
        "  # or, on a non-system Python:  pip install evdev\n"
        "or run tools/gamepad/setup_gamepad.sh which installs it for you.\n"
    )
    raise


CALIB_DIR = Path(__file__).resolve().parent / 'calibrations'

MAP = {
    'steer_axis':    evdev.ecodes.ABS_X,    # Left stick X  → steer
    'throttle_axis': evdev.ecodes.ABS_Y,    # Left stick Y  → throttle (+fwd / -rev)
    # E-stop: TL held AND both D-pad axes non-zero (any diagonal) — latching
    'estop_btn':   evdev.ecodes.BTN_TL,
    'estop_axes': (evdev.ecodes.ABS_HAT0X, evdev.ecodes.ABS_HAT0Y),
    'arm_btn':     evdev.ecodes.BTN_TL,     # L1 held = dead-man arm (test tool only)
    # Re-arm: both thumbsticks clicked simultaneously
    'rearm_combo':  (evdev.ecodes.BTN_THUMBL, evdev.ecodes.BTN_THUMBR),
}

# Physical button labels for G3 V2. A/B follow the Xbox convention (SOUTH=A,
# EAST=B), but X/Y are in Nintendo positions (NORTH=X, WEST=Y) — that's just
# how the controller is silkscreened. The G3 V2 also reports Select via
# KEY_HOMEPAGE instead of BTN_SELECT.
_BTN_DISPLAY_NAMES = {
    evdev.ecodes.BTN_SOUTH: 'A',
    evdev.ecodes.BTN_EAST:  'B',
    evdev.ecodes.BTN_NORTH: 'X',
    evdev.ecodes.BTN_WEST:  'Y',
}
# Display-only rename: G3 V2's Select emits KEY_BACK (158), so override its
# label without touching the evdev code itself.
_kb = getattr(evdev.ecodes, 'KEY_BACK', None)
if _kb is not None:
    _BTN_DISPLAY_NAMES[_kb] = 'SELECT'
del _kb

_GAMEPAD_KEYWORDS = ('xbox', 'x-box', 'machenike', 'gamepad', 'game pad',
                     'controller', 'joystick', 'g3')

DEADZONE = 0.06


# ── Device discovery ──────────────────────────────────────────────────────────

def dev_path(dev):
    """Filesystem path of an evdev InputDevice.

    evdev >= 1.0 exposes `.path`; the Jetson's evdev 0.7.0 (Python 3.6) uses
    `.fn`. Support both so the same code runs on host and robot.
    """
    return getattr(dev, 'path', None) or getattr(dev, 'fn', '?')


def list_devices():
    devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
    if not devs:
        print('No input devices found.')
        return
    for d in devs:
        print('{:18s}  {:32s}  {}'.format(dev_path(d), d.name, d.phys or ''))


def find_gamepad():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if any(kw in d.name.lower() for kw in _GAMEPAD_KEYWORDS):
            return d
        d.close()
    return None


# ── Calibration file ──────────────────────────────────────────────────────────

def _calib_path(device_name):
    safe = re.sub(r'[^\w\-]', '_', device_name).strip('_')
    return CALIB_DIR / f'{safe}_calib.json'


def load_calibration(device_name):
    path = _calib_path(device_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return {int(k): v for k, v in data.get('axes', {}).items()}
    except Exception:
        return None


def save_calibration(device_name, axes_calib):
    path = _calib_path(device_name)
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        'device': device_name,
        'axes':   {str(k): v for k, v in axes_calib.items()},
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalize_stick(raw, lo, hi, center):
    """Map raw stick value to [-1, +1] using separate half-ranges per direction."""
    half_pos = hi - center
    half_neg = center - lo
    scale    = max(half_pos, half_neg, 1)
    delta    = raw - center
    if -DEADZONE < delta / scale < DEADZONE:
        return 0.0
    if delta >= 0:
        v =  delta / half_pos if half_pos > 0 else 0.0
    else:
        v =  delta / half_neg if half_neg > 0 else 0.0
    return max(-1.0, min(1.0, v))


def normalize_trigger(raw, lo, hi):
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (raw - lo) / float(hi - lo)))


def _abs_name(code):
    n = evdev.ecodes.ABS.get(code, f'ABS_{code}')
    if isinstance(n, (list, tuple)):
        n = n[0]
    return str(n)


def _btn_name(code):
    if code in _BTN_DISPLAY_NAMES:
        return _BTN_DISPLAY_NAMES[code]
    n = evdev.ecodes.BTN.get(code) or evdev.ecodes.keys.get(code, f'KEY_{code}')
    if isinstance(n, (list, tuple)):
        n = n[0]
    return str(n).replace('BTN_', '').replace('KEY_', '')


# ── Introspection ─────────────────────────────────────────────────────────────

# Capabilities the G3 V2 advertises but never actually emits events for.
# Filter them out so they don't clutter the UI.
_AXIS_DENYLIST = set()
_BUTTON_DENYLIST = {
    evdev.ecodes.BTN_SELECT,
    evdev.ecodes.BTN_C,
    evdev.ecodes.BTN_Z,
    evdev.ecodes.BTN_MODE,
}
_kh = getattr(evdev.ecodes, 'KEY_HOMEPAGE', None)
if _kh is not None:
    _BUTTON_DENYLIST.add(_kh)
del _kh


def get_abs_info(dev):
    pairs = dev.capabilities(absinfo=True).get(evdev.ecodes.EV_ABS, [])
    return [(code, info) for code, info in pairs if code not in _AXIS_DENYLIST]


# Some gamepads report Select / Home / Start via KEY_* codes that fall below
# BTN_MISC and would otherwise be filtered out. Include them explicitly so they
# show up alongside the BTN_* buttons. Anything in _BUTTON_DENYLIST is dropped.
_KEY_BUTTON_FALLBACKS = {
    code for code in (
        getattr(evdev.ecodes, 'KEY_BACK',     None),   # Select / Back / Share
        getattr(evdev.ecodes, 'KEY_HOMEPAGE', None),   # Home / Guide
        getattr(evdev.ecodes, 'KEY_MENU',     None),   # Menu / Start
    ) if code is not None
}


def get_btn_codes(dev):
    all_keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
    return sorted(c for c in all_keys
                  if (c >= evdev.ecodes.BTN_MISC or c in _KEY_BUTTON_FALLBACKS)
                  and c not in _BUTTON_DENYLIST)


# ── State ─────────────────────────────────────────────────────────────────────

class GamepadState:
    def __init__(self, dev, calib):
        self.dev       = dev
        self.abs_info  = {code: info for code, info in get_abs_info(dev)}
        self.btn_codes = get_btn_codes(dev)
        self.axes      = {}
        self.buttons   = {}
        self.event_times   = []
        self.last_event_t  = time.monotonic()
        self.estop_latched = False
        self.calib         = calib or {}   # {code: {lo, hi, center}}

    def _cal(self, code):
        """Return (lo, hi, center) from calibration or absinfo fallback."""
        info = self.abs_info.get(code)
        if code in self.calib:
            c = self.calib[code]
            return c['lo'], c['hi'], c['center']
        if info is None:
            return 0, 1, 0
        lo, hi = info.min, info.max
        if info.min < 0:
            center = info.value
        elif code in (MAP['steer_axis'], MAP['throttle_axis']):
            # Unsigned stick axes (min=0) need a midpoint center so
            # normalize_stick can be applied correctly without calibration.
            center = (lo + hi) // 2
        else:
            center = lo
        return lo, hi, center

    def _estop_combo_active(self):
        btn_ok  = self.buttons.get(MAP['estop_btn'], False)
        axes_ok = all(self.axes.get(ax, 0) != 0 for ax in MAP['estop_axes'])
        return btn_ok and axes_ok

    def feed(self, event):
        t = time.monotonic()
        self.event_times.append(t)
        self.event_times = [x for x in self.event_times if t - x <= 1.0]
        self.last_event_t = t

        if event.type == evdev.ecodes.EV_ABS:
            self.axes[event.code] = event.value
        elif event.type == evdev.ecodes.EV_KEY:
            self.buttons[event.code] = bool(event.value)

        if self._estop_combo_active():
            self.estop_latched = True
        elif (event.type == evdev.ecodes.EV_KEY
              and all(self.buttons.get(b, False) for b in MAP['rearm_combo'])):
            self.estop_latched = False

    def axis_norm(self, code):
        lo, hi, center = self._cal(code)
        raw = self.axes.get(code, center)
        if center > lo:
            val = normalize_stick(raw, lo, hi, center)
        else:
            val = normalize_trigger(raw, lo, hi)
        # ABS_Y decreases when pushed forward, so negate to make
        # forward = positive and backward = negative.
        if code == MAP['throttle_axis']:
            val = -val
        return val

    def steer_norm(self):    return self.axis_norm(MAP['steer_axis'])
    def throttle_norm(self): return self.axis_norm(MAP['throttle_axis'])

    def button(self, code):     return self.buttons.get(code, False)
    def events_per_sec(self):   return len(self.event_times)
    def age_since_last(self):   return time.monotonic() - self.last_event_t

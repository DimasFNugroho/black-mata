#!/usr/bin/env python3
"""
dashboard/server.py - Black-Mata Operator Drive Dashboard.

Run:
    python3 tools/dashboard/server.py
    python3 tools/dashboard/server.py --port /dev/ttyACM0 --ui-port 8082

Then open:  http://<jetson-ip>:8082
"""

import argparse
import glob
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gamepad'))

from software.robot.serial_driver import SerialDriver
from software.robot.ackermann import Ackermann, AckermannConfig

# Gamepad support is optional — the dashboard works without it. If evdev
# isn't installed (e.g. on a workstation that won't ever have a gamepad
# attached), the reader is simply never started and the /api/gamepad/state
# endpoint always reports {connected: false}.
_GAMEPAD_AVAILABLE = False
try:
    from gamepad_core import (find_gamepad, load_calibration, GamepadState,
                              dev_path, MAP as GP_MAP)
    _GAMEPAD_AVAILABLE = True
except ImportError:
    print('[Gamepad] evdev not available — gamepad support disabled.')
    print('          Install with: sudo apt install python3-evdev')

import select

_driver         = None
_gamepad_reader = None
_last_drive_t   = 0.0

DEFAULTS = {
    'wheelbase':             0.20,
    'track_width':           0.15,
    'max_steer_deg':         30.0,
    'max_wheel_speed_ticks': 300,
    'steer_center_ticks':    512,
    'steer_dir':             [1, -1, -1,  1],
    'drive_dir':             [1, -1,  1, -1],
    'steer_offset_deg':      [0.0, 0.0, 0.0, 0.0],
    'servo_ids':             [4, 2, 8, 6, 3, 1, 7, 5],
    'batt_max_v':   12.6,
    'batt_ok_v':    11.0,
    'batt_low_v':   10.2,
    'batt_critical_v': 9.6,
}

CONFIG_PATH = Path(__file__).parents[1] / 'ackermann_ui' / 'ackermann_config.json'

_camera_url = 'http://localhost:8083/stream'


def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return DEFAULTS.copy()


def _build_ackermann(c):
    cfg = AckermannConfig()
    cfg.wheelbase             = float(c.get('wheelbase',             DEFAULTS['wheelbase']))
    cfg.track_width           = float(c.get('track_width',           DEFAULTS['track_width']))
    cfg.max_steer_deg         = float(c.get('max_steer_deg',         DEFAULTS['max_steer_deg']))
    cfg.max_wheel_speed_ticks = min(int(c.get('max_wheel_speed_ticks', DEFAULTS['max_wheel_speed_ticks'])), 1023)
    cfg.steer_center_ticks    = int(  c.get('steer_center_ticks',    DEFAULTS['steer_center_ticks']))
    cfg.steer_dir             = list( c.get('steer_dir',             DEFAULTS['steer_dir']))
    cfg.drive_dir             = list( c.get('drive_dir',             DEFAULTS['drive_dir']))
    cfg.steer_offset_deg      = list( c.get('steer_offset_deg',      DEFAULTS['steer_offset_deg']))
    cfg.servo_ids             = list( c.get('servo_ids',             DEFAULTS['servo_ids']))
    return cfg


_STATIC_DIR = Path(__file__).resolve().parent / 'static'


def _build_html(camera_url):
    return (_STATIC_DIR / 'index.html').read_text().replace('__CAMERA_URL__', camera_url)


# ── Gamepad reader ────────────────────────────────────────────────────────────

class GamepadReader:
    """Background thread that owns an evdev gamepad and maintains a
    thread-safe GamepadState. Reconnects automatically when the controller
    disappears and reappears."""

    RECONNECT_SECS = 2.0   # poll for a new device this often when disconnected
    POLL_TIMEOUT_S = 0.1   # select() timeout while reading

    def __init__(self):
        self._lock    = threading.Lock()
        self._dev     = None     # evdev.InputDevice or None
        self._state   = None     # GamepadState or None
        self._running = False
        self._thread  = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='gamepad-reader')
        self._thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            if self._dev is not None:
                try: self._dev.close()
                except Exception: pass
                self._dev = None
                self._state = None

    def _loop(self):
        while self._running:
            if self._dev is None:
                try:
                    dev = find_gamepad()
                except Exception as e:
                    print(f'[Gamepad] find_gamepad error: {e}')
                    dev = None
                if dev is None:
                    time.sleep(self.RECONNECT_SECS)
                    continue
                with self._lock:
                    self._dev   = dev
                    self._state = GamepadState(dev, load_calibration(dev.name))
                print(f'[Gamepad] Connected: {dev.name} ({dev_path(dev)})')
                continue

            try:
                r, _, _ = select.select([self._dev.fd], [], [], self.POLL_TIMEOUT_S)
                if r:
                    for ev in self._dev.read():
                        with self._lock:
                            if self._state is not None:
                                self._state.feed(ev)
            except OSError:
                with self._lock:
                    print(f'[Gamepad] Disconnected: {dev_path(self._dev)}')
                    try: self._dev.close()
                    except Exception: pass
                    self._dev   = None
                    self._state = None
                time.sleep(1.0)

    def snapshot(self):
        """Return a JSON-friendly snapshot of the current state."""
        with self._lock:
            dev, s = self._dev, self._state
            if dev is None or s is None:
                return {'connected': False}
            return {
                'connected':       True,
                'name':            dev.name,
                'path':            dev_path(dev),
                'steer':           round(s.steer_norm(),    4),
                'throttle':        round(s.throttle_norm(), 4),
                'deadman':         bool(s.button(GP_MAP['arm_btn'])),   # L1 held
                'buttons':         {str(k): bool(v) for k, v in s.buttons.items()},
                'estop_combo':     s._estop_combo_active(),
                'estop_latched':   s.estop_latched,
                'rearm_combo':     all(s.buttons.get(b, False)
                                       for b in GP_MAP['rearm_combo']),  # LS + RS
                'events_per_sec':  s.events_per_sec(),
                'age_s':           round(s.age_since_last(), 3),
            }


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Client closed the connection before we finished writing.
            # Normal with high-frequency pollers; nothing to do.
            pass

    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        self.path = self.path.split('?')[0]
        if self.path == '/':
            body = _build_html(_camera_url).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith('/static/'):
            rel = self.path[len('/static/'):]
            target = (_STATIC_DIR / rel).resolve()
            if _STATIC_DIR not in target.parents or not target.is_file():
                self._send_json({'error': 'not found'}, 404)
                return
            ctype = {
                '.html': 'text/html; charset=utf-8',
                '.css':  'text/css; charset=utf-8',
                '.js':   'application/javascript; charset=utf-8',
            }.get(target.suffix, 'application/octet-stream')
            body = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == '/camera/ping':
            try:
                health_url = _camera_url.replace('/stream', '/health')
                req = urllib.request.urlopen(health_url, timeout=2)
                req.close()
                self.send_response(200)
                self.end_headers()
            except Exception:
                self.send_response(503)
                self.end_headers()

        elif self.path == '/camera':
            try:
                req = urllib.request.urlopen(_camera_url, timeout=5)
                self.send_response(200)
                self.send_header('Content-Type', req.headers.get('Content-Type',
                                 'multipart/x-mixed-replace; boundary=frame'))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                try:
                    while True:
                        chunk = req.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            except Exception as e:
                self._send_json({'error': 'camera unavailable: ' + str(e)}, 503)

        elif self.path == '/api/gamepad/state':
            if _gamepad_reader is None:
                self._send_json({'connected': False})
                return
            self._send_json(_gamepad_reader.snapshot())

        elif self.path == '/config':
            self._send_json(_load_config())

        elif self.path == '/state':
            if _driver is None:
                self._send_json({'connected': False})
                return
            s = _driver.get_state()
            if s is None:
                self._send_json({'connected': True, 'state': None})
                return
            servos = [{
                'id':        sv.servo_id,
                'available': sv.available,
                'mode':      'WHEEL' if sv.mode else 'JOINT',
                'pos':       sv.pos,
                'speed':     sv.speed,
                'temp_c':    sv.temperature,
                'volt_v':    sv.voltage,
            } for sv in s.servos]
            self._send_json({
                'connected': True,
                'state': {'seq': s.seq, 'e_stop': s.e_stop, 'servos': servos},
            })

        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        global _last_drive_t
        data = self._read_json()

        if self.path == '/drive':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            cfg       = _build_ackermann(_load_config())
            steer_deg = float(data.get('steer_deg', 0))
            speed_mps = float(data.get('speed_mps', 0))
            steer_deg = max(-cfg.max_steer_deg, min(cfg.max_steer_deg, steer_deg))
            speed_mps = max(-1.0, min(1.0, speed_mps))
            targets   = Ackermann(cfg).compute(steer_deg, speed_mps)
            _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            _last_drive_t = time.monotonic()
            self._send_json({'ok': True, 'steer_deg': steer_deg, 'speed_mps': speed_mps})

        elif self.path == '/estop':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            _driver.send_estop()
            self._send_json({'status': 'e-stop sent'})

        else:
            self._send_json({'error': 'not found'}, 404)


# ── Keepalive ─────────────────────────────────────────────────────────────────

def _keepalive_loop():
    while True:
        time.sleep(0.2)
        if _driver is None:
            continue
        if time.monotonic() - _last_drive_t > 0.3:
            try:
                cfg     = _build_ackermann(_load_config())
                targets = Ackermann(cfg).estop_targets()
                _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            except Exception:
                _driver.send_estop()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _driver, _gamepad_reader, _camera_url

    parser = argparse.ArgumentParser(description='Black-Mata Drive Dashboard')
    parser.add_argument('--port',       '-p', default=None,
                        help='Serial port (auto-detected if omitted)')
    parser.add_argument('--baud',       '-b', type=int, default=115200)
    parser.add_argument('--ui-port',    '-u', type=int, default=8082,
                        help='Dashboard HTTP port (default: 8082)')
    parser.add_argument('--camera-url', '-c', default='http://localhost:8083/stream',
                        help='MJPEG stream URL (default: http://localhost:8083/stream)')
    args = parser.parse_args()
    _camera_url = args.camera_url

    port = args.port
    if not port:
        candidates = (glob.glob('/dev/opencm')
                      + glob.glob('/dev/serial/by-id/*ROBOTIS*')
                      + sorted(glob.glob('/dev/ttyACM*')))
        port = candidates[0] if candidates else None

    if port:
        print('Connecting to robot on {}...'.format(port))
        _driver = SerialDriver(port, args.baud)
        _driver.connect()
        _driver.start()
        _driver.send_estop()
        threading.Thread(target=_keepalive_loop, daemon=True, name='keepalive').start()
        print('Robot connected.')
    else:
        print('No serial port found — running in simulation mode (no robot).')

    if _GAMEPAD_AVAILABLE:
        _gamepad_reader = GamepadReader()
        _gamepad_reader.start()

    print('Camera : {}'.format(_camera_url))
    print('Open   : http://localhost:{}'.format(args.ui_port))
    server = ThreadingHTTPServer(('0.0.0.0', args.ui_port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        if _gamepad_reader:
            _gamepad_reader.stop()
        if _driver:
            _driver.send_estop()
            _driver.stop()
            _driver.close()


if __name__ == '__main__':
    main()

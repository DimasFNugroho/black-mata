#!/usr/bin/env python3
"""
ackermann_ui/server.py - Browser-based Ackermann config and test UI.

Uses only Python stdlib (http.server) - no FastAPI or uvicorn required.

Run:
    python3 tools/ackermann_ui/server.py
    python3 tools/ackermann_ui/server.py --port /dev/ttyACM1

Then open:  http://<jetson-ip>:8080
"""

import argparse
import glob
import json
import math
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.robot.serial_driver import SerialDriver, ServoCmd, NUM_SERVOS
from software.robot.ackermann import Ackermann, AckermannConfig

CONFIG_PATH = Path(__file__).parent / 'ackermann_config.json'

_driver         = None   # SerialDriver, set in main()
_current_cfg    = None   # last AckermannConfig used for a drive command
_last_drive_t   = 0.0   # monotonic time of last /drive POST

# ── Steering profile state ─────────────────────────────────────────────────────
_profile_lock   = threading.Lock()
_profile_active = False   # True while a trapezoidal profile is executing
_profile_steer  = 0.0    # continuously updated steer angle during profile
_current_steer  = 0.0    # steer angle after the last send/profile completes


# ── Ackermann helpers ──────────────────────────────────────────────────────────

def _build_cfg(c):
    def _f(key, default): return float(c.get(key) if c.get(key) is not None else default)
    def _i(key, default): return int(c.get(key) if c.get(key) is not None else default)
    def _l(key, default): return list(c.get(key) if c.get(key) is not None else default)
    cfg = AckermannConfig()
    cfg.wheelbase             = _f('wheelbase',             0.20)
    cfg.track_width           = _f('track_width',           0.15)
    cfg.max_steer_deg         = _f('max_steer_deg',         30.0)
    cfg.max_speed_mps         = _f('max_speed_mps',         0.5)
    cfg.max_wheel_speed_ticks = _i('max_wheel_speed_ticks', 300)
    cfg.steer_center_ticks    = _i('steer_center_ticks',    512)
    cfg.steer_dir             = _l('steer_dir',        [1, -1, -1,  1])
    cfg.drive_dir             = _l('drive_dir',        [1, -1,  1, -1])
    cfg.steer_offset_deg      = _l('steer_offset_deg', [0.0, 0.0, 0.0, 0.0])
    cfg.servo_ids             = _l('servo_ids',        [4, 2, 8, 6, 3, 1, 7, 5])
    # Profile parameters (not AckermannConfig fields — stored alongside)
    cfg._steer_rate_deg_s     = _f('steer_rate_deg_s',   30.0)
    cfg._steer_accel_deg_s2   = _f('steer_accel_deg_s2', 60.0)
    return cfg


def _compute_result(steer_deg, speed_mps, cfg):
    ack     = Ackermann(cfg)
    targets = ack.compute(steer_deg, speed_mps)
    labels  = ['FL', 'FR', 'RL', 'RR']

    # Compute wheel angles from pure Ackermann geometry — independent of servo calibration
    δ  = max(-cfg.max_steer_deg, min(cfg.max_steer_deg, steer_deg))
    L2 = cfg.wheelbase / 2.0
    W2 = cfg.track_width / 2.0
    if abs(δ) < 0.5:
        steer_angles = [0.0, 0.0, 0.0, 0.0]
    else:
        δ_rad     = math.radians(abs(δ))
        sign      = 1 if δ > 0 else -1
        R         = L2 / math.tan(δ_rad)
        outer_abs = math.degrees(math.atan2(L2, R + W2))
        inner_abs = math.degrees(math.atan2(L2, R - W2))
        if sign > 0:  # right turn: FL/RL outer, FR/RR inner
            fl_deg, fr_deg =  outer_abs,  inner_abs
        else:          # left turn:  FL/RL inner, FR/RR outer
            fl_deg, fr_deg = -inner_abs, -outer_abs
        steer_angles = [
            round( fl_deg, 2),
            round( fr_deg, 2),
            round(-fl_deg, 2),
            round(-fr_deg, 2),
        ]

    drive_info = []
    for i in range(4):
        raw = targets[4 + i].target
        if raw == 0:
            mag, direction = 0, 'STOP'
        elif raw < 1024:
            mag, direction = raw, 'CCW'
        else:
            mag, direction = raw - 1024, 'CW'
        drive_info.append({'mag': mag, 'dir': direction, 'raw': raw})

    wheels = []
    for i in range(4):
        wheels.append({
            'label':       labels[i],
            'steer_angle': steer_angles[i],
            'steer_tick':  targets[i].target,
            'drive_mag':   drive_info[i]['mag'],
            'drive_dir':   drive_info[i]['dir'],
            'drive_raw':   drive_info[i]['raw'],
        })

    d = abs(steer_deg)
    if d >= 0.5:
        L2 = cfg.wheelbase / 2
        R  = round(L2 / math.tan(math.radians(d)), 3)
    else:
        R  = None

    # AX-12A position space per wheel (0-1023 ticks = 0-300 deg)
    # neutral_tick: where the wheel sits at 0 steer (including offset)
    # min/max reachable: neutral +/- max_steer range, clamped to 0-1023
    position_space = []
    for i in range(4):
        tpd     = cfg.ticks_per_deg
        neutral = int(round(cfg.steer_center_ticks + cfg.steer_dir[i] * cfg.steer_offset_deg[i] * tpd))
        span    = int(round(cfg.max_steer_deg * tpd))
        position_space.append({
            'neutral_tick': max(0, min(1023, neutral)),
            'min_tick':     max(0, neutral - span),
            'max_tick':     min(1023, neutral + span),
            'current_tick': wheels[i]['steer_tick'],
        })

    return {
        'wheels':         wheels,
        'position_space': position_space,
        'turning_radius': R,
        'steer_clamped':  max(-cfg.max_steer_deg, min(cfg.max_steer_deg, steer_deg)),
        'speed_clamped':  max(-1.0, min(1.0, speed_mps / cfg.max_speed_mps)) * cfg.max_speed_mps,
    }


def _default_config():
    return {
        'wheelbase': 0.20, 'track_width': 0.15,
        'max_steer_deg': 30.0, 'max_speed_mps': 0.5,
        'max_wheel_speed_ticks': 300, 'steer_center_ticks': 512,
        'steer_dir': [1, -1, -1, 1], 'drive_dir': [1, -1, 1, -1],
        'steer_offset_deg': [0.0, 0.0, 0.0, 0.0],
        'servo_ids': [4, 2, 8, 6, 3, 1, 7, 5],
        'steer_rate_deg_s': 30.0, 'steer_accel_deg_s2': 60.0,
    }


# ── Trapezoidal steering profile ──────────────────────────────────────────────

def _trap_pos(t, d, t_ramp, t_flat, accel, rate):
    """Return distance covered along trapezoidal profile at time t."""
    if t <= t_ramp:
        return 0.5 * accel * t * t
    elif t <= t_ramp + t_flat:
        d_ramp = 0.5 * accel * t_ramp * t_ramp
        return d_ramp + rate * (t - t_ramp)
    else:
        t_down = t - t_ramp - t_flat
        d_ramp = 0.5 * accel * t_ramp * t_ramp
        d_flat = rate * t_flat
        return d_ramp + d_flat + rate * t_down - 0.5 * accel * t_down * t_down


def _run_profile(start_steer, target_steer, speed_mps, cfg):
    """
    Execute a trapezoidal steering profile in a background thread.
    Sends CMD frames at 25 Hz, stamps _last_drive_t to feed the watchdog.
    Sets _profile_active = False when done.
    """
    global _profile_active, _profile_steer, _current_steer, _last_drive_t

    rate  = max(1.0, cfg._steer_rate_deg_s)
    accel = max(1.0, cfg._steer_accel_deg_s2)
    delta = target_steer - start_steer
    d     = abs(delta)
    sign  = 1.0 if delta >= 0 else -1.0

    if d < 0.5:
        with _profile_lock:
            _current_steer  = target_steer
            _profile_steer  = target_steer
            _profile_active = False
        return

    d_ramp = rate * rate / (2.0 * accel)
    if d < 2.0 * d_ramp:
        # Triangle profile — cannot reach max rate
        t_ramp = math.sqrt(d / accel)
        t_flat = 0.0
    else:
        t_ramp = rate / accel
        t_flat = (d - 2.0 * d_ramp) / rate

    t_total = 2.0 * t_ramp + t_flat
    dt      = 0.04   # 25 Hz

    ack = Ackermann(cfg)
    t   = 0.0
    while True:
        with _profile_lock:
            if not _profile_active:
                return  # aborted by estop
        pos    = _trap_pos(min(t, t_total), d, t_ramp, t_flat, accel, rate)
        steer  = max(-cfg.max_steer_deg,
                     min(cfg.max_steer_deg, start_steer + sign * pos))

        with _profile_lock:
            _profile_steer = steer
        _last_drive_t = time.monotonic()
        _driver.send_frame(ack.compute(steer, speed_mps), servo_ids=cfg.servo_ids)

        if t >= t_total:
            break
        time.sleep(dt)
        t += dt

    # Ensure exact target on final frame
    with _profile_lock:
        _profile_steer  = target_steer
        _current_steer  = target_steer
        _profile_active = False
    _last_drive_t = time.monotonic()
    _driver.send_frame(ack.compute(target_steer, speed_mps), servo_ids=cfg.servo_ids)


# ── HTTP handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence default access log

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path == '/':
            body = HTML_PAGE.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == '/config':
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text())
            else:
                data = _default_config()
            self._send_json(data)

        elif self.path == '/state':
            if _driver is None:
                self._send_json({'connected': False})
                return
            s = _driver.get_state()
            if s is None:
                self._send_json({'connected': True, 'state': None})
                return
            servos = [{
                'id': sv.servo_id, 'available': sv.available,
                'mode': 'WHEEL' if sv.mode else 'JOINT',
                'pos': sv.pos, 'speed': sv.speed,
                'temp_c': sv.temperature, 'volt_v': sv.voltage,
            } for sv in s.servos]
            # Decode FL steer position and FL drive speed back to engineering units
            feedback = None
            cfg = _current_cfg
            if cfg is not None:
                try:
                    fl_s = s.servos[cfg.servo_ids[0] - 1]
                    if fl_s.available and cfg.steer_dir[0] != 0:
                        raw_angle = (fl_s.pos - cfg.steer_center_ticks) / (cfg.steer_dir[0] * cfg.ticks_per_deg)
                        steer_fb = raw_angle - cfg.steer_offset_deg[0]
                        steer_fb = max(-cfg.max_steer_deg, min(cfg.max_steer_deg, steer_fb))
                    else:
                        steer_fb = 0.0
                    fl_d = s.servos[cfg.servo_ids[4] - 1]
                    if fl_d.available:
                        raw = fl_d.speed  # 0-2047 AX-12A PRESENT_SPEED encoding
                        if raw == 0 or raw == 1024:
                            frac = 0.0
                        elif raw < 1024:
                            frac = (raw / float(cfg.max_wheel_speed_ticks)) * cfg.drive_dir[0]
                        else:
                            frac = -((raw - 1024) / float(cfg.max_wheel_speed_ticks)) * cfg.drive_dir[0]
                        speed_fb = max(-cfg.max_speed_mps, min(cfg.max_speed_mps, frac * cfg.max_speed_mps))
                    else:
                        speed_fb = 0.0
                    feedback = {'steer_deg': round(steer_fb, 2), 'speed_mps': round(speed_fb, 3)}
                except Exception:
                    feedback = None
            self._send_json({'connected': True, 'feedback': feedback,
                             'state': {'seq': s.seq, 'e_stop': s.e_stop, 'servos': servos}})

        elif self.path == '/ports':
            candidates = (glob.glob('/dev/opencm')
                          + glob.glob('/dev/serial/by-id/*ROBOTIS*')
                          + sorted(glob.glob('/dev/ttyACM*')))
            self._send_json({'ports': candidates})

        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        global _current_cfg, _last_drive_t, _current_steer, _profile_active, _profile_steer
        data = self._read_json()

        if self.path == '/drive':
            # Watchdog feed — JS calls this at 10 Hz while driving is active
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            with _profile_lock:
                profiling = _profile_active
            if profiling:
                # Profile thread is in charge; just acknowledge
                self._send_json({'driving': True, 'profiling': True})
                return
            cfg = _build_cfg(data.get('config', {}))
            _current_cfg = cfg
            _last_drive_t = time.monotonic()
            steer_deg = float(data.get('steer_deg', 0))
            speed_mps = float(data.get('speed_mps', 0))
            _current_steer = steer_deg
            targets = Ackermann(cfg).compute(steer_deg, speed_mps)
            _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            result = _compute_result(steer_deg, speed_mps, cfg)
            result['driving'] = True
            self._send_json(result)

        elif self.path == '/send_profile':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            with _profile_lock:
                if _profile_active:
                    self._send_json({'error': 'Profile already running'}, 409)
                    return
                _profile_active = True

            cfg         = _build_cfg(data.get('config', {}))
            _current_cfg = cfg
            target_steer = float(data.get('steer_deg', 0))
            speed_mps    = float(data.get('speed_mps', 0))

            with _profile_lock:
                start_steer = _current_steer

            # Compute expected duration for the UI progress bar
            rate  = max(1.0, cfg._steer_rate_deg_s)
            accel = max(1.0, cfg._steer_accel_deg_s2)
            d     = abs(target_steer - start_steer)
            d_ramp = rate * rate / (2.0 * accel)
            if d < 0.5:
                duration_ms = 0
            elif d < 2.0 * d_ramp:
                duration_ms = int(2.0 * math.sqrt(d / accel) * 1000)
            else:
                t_ramp = rate / accel
                t_flat = (d - 2.0 * d_ramp) / rate
                duration_ms = int((2.0 * t_ramp + t_flat) * 1000)

            threading.Thread(
                target=_run_profile,
                args=(start_steer, target_steer, speed_mps, cfg),
                daemon=True, name='steer-profile'
            ).start()

            self._send_json({
                'active': True,
                'from_deg': round(start_steer, 2),
                'to_deg':   round(target_steer, 2),
                'duration_ms': duration_ms,
            })

        elif self.path == '/profile_status':
            with _profile_lock:
                active = _profile_active
                steer  = _profile_steer
            self._send_json({'active': active, 'steer_deg': round(steer, 2)})

        elif self.path == '/compute':
            cfg    = _build_cfg(data.get('config', {}))
            result = _compute_result(
                float(data.get('steer_deg', 0)),
                float(data.get('speed_mps', 0)),
                cfg)
            self._send_json(result)

        elif self.path == '/send':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            cfg     = _build_cfg(data.get('config', {}))
            ack     = Ackermann(cfg)
            targets = ack.compute(
                float(data.get('steer_deg', 0)),
                float(data.get('speed_mps', 0)))
            _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            result = _compute_result(
                float(data.get('steer_deg', 0)),
                float(data.get('speed_mps', 0)),
                cfg)
            result['sent'] = True
            self._send_json(result)

        elif self.path == '/estop':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            with _profile_lock:
                _profile_active = False  # signal profile thread to stop feeding
            _driver.send_estop()
            self._send_json({'status': 'e-stop sent'})

        elif self.path == '/torque':
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            on = bool(data.get('on', True))
            if on:
                # Enable torque: hold position (steer=0, speed=0) with torque on
                cfg = _build_cfg(data.get('config', {}))
                targets = Ackermann(cfg).compute(0.0, 0.0)
                _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            else:
                _driver.send_estop()
            self._send_json({'torque': on})

        elif self.path == '/record_centers':
            # Read current steering servo positions and compute steer_offset_deg.
            # Call this after torque is off and user has manually aligned wheels straight.
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            s = _driver.get_state()
            if s is None:
                self._send_json({'error': 'No STATE frame received yet'}, 503)
                return
            cfg     = _build_cfg(data.get('config', {}))
            offsets = []
            details = []
            for i in range(4):
                sid = cfg.servo_ids[i]
                sv  = s.servos[sid - 1]
                if sv.available and cfg.steer_dir[i] != 0:
                    offset = (sv.pos - cfg.steer_center_ticks) / (cfg.steer_dir[i] * cfg.ticks_per_deg)
                    offsets.append(round(offset, 2))
                    details.append({'id': sid, 'pos': sv.pos, 'offset_deg': round(offset, 2)})
                else:
                    offsets.append(0.0)
                    details.append({'id': sid, 'pos': None, 'offset_deg': 0.0})
            self._send_json({'steer_offset_deg': offsets, 'details': details})

        elif self.path == '/nudge':
            # Nudge a single servo to help identify its physical location.
            # Sends JOINT+torque-on to one servo ID, all others torque-off.
            # Client calls /nudge repeatedly at ~10Hz to feed the watchdog.
            if _driver is None:
                self._send_json({'error': 'No robot connected'}, 503)
                return
            sid      = int(data.get('servo_id', 1))
            nudge    = int(data.get('nudge_ticks', 30))
            center   = int(data.get('center_ticks', 512))
            target   = max(0, min(1023, center + nudge))
            cmds = []
            for slot in range(1, NUM_SERVOS + 1):
                if slot == sid:
                    cmds.append(ServoCmd(mode=0, enable_torque=1, target=target))
                else:
                    cmds.append(ServoCmd(mode=0, enable_torque=0, target=center))
            _driver.send_frame(cmds)
            self._send_json({'nudging': sid, 'target_tick': target})

        elif self.path == '/config':
            cfg = data if data else _default_config()
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
            self._send_json({'saved': str(CONFIG_PATH)})

        else:
            self._send_json({'error': 'not found'}, 404)


# ── HTML page ──────────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ackermann Config UI - Black-Mata</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: monospace; background: #111; color: #ddd; font-size: 14px; }
  h2 { color: #7cf; margin-bottom: 8px; }
  h3 { color: #adf; margin-bottom: 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }
  #layout { display: grid; grid-template-columns: 420px 1fr; grid-template-rows: auto auto auto; gap: 12px; padding: 12px; }
  .card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 14px; }
  #viz-card { grid-row: 1 / 4; }
  svg { display: block; margin: 0 auto; }
  .slider-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .slider-row label { width: 90px; color: #aaa; }
  .slider-row input[type=range] { flex: 1; accent-color: #7cf; }
  .slider-row .val { width: 70px; text-align: right; color: #fff; font-weight: bold; }
  .btn { padding: 7px 16px; border: none; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 13px; font-weight: bold; }
  .btn-send  { background: #2a7; color: #fff; }
  .btn-estop { background: #c33; color: #fff; }
  .btn-save    { background: #46a; color: #fff; }
  .btn-load    { background: #555; color: #fff; }
  .btn-torque-on  { background: #2a7; color: #fff; }
  .btn-torque-off { background: #555; color: #aaa; border: 1px solid #888; }
  .btn:hover { opacity: 0.85; }
  .btn:active { opacity: 0.7; }
  .btn-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  table { width: 100%; border-collapse: collapse; }
  th { color: #7cf; font-weight: normal; text-align: left; padding: 3px 6px; border-bottom: 1px solid #333; }
  td { padding: 3px 6px; }
  tr:nth-child(even) td { background: #222; }
  .hl { color: #7fc; font-weight: bold; }
  .cfg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
  .cfg-row { display: flex; align-items: center; gap: 6px; }
  .cfg-row label { width: 160px; color: #aaa; font-size: 12px; }
  .cfg-row input { width: 80px; background: #222; border: 1px solid #444; color: #fff; padding: 3px 6px; border-radius: 3px; font-family: monospace; }
  .cfg-row input:focus { outline: none; border-color: #7cf; }
  #status-bar { padding: 6px 12px; background: #0a2a0a; border-top: 1px solid #333; font-size: 12px; color: #8a8; }
  #status-bar.err { background: #2a0a0a; color: #f88; }
</style>
</head>
<body>

<!-- Profile splash overlay -->
<div id="profile-splash" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#1c1c2e;border:1px solid #555;border-radius:10px;padding:32px 36px;text-align:center;min-width:300px;box-shadow:0 8px 32px #000a;">
    <div style="color:#fa8;font-size:17px;font-weight:bold;margin-bottom:8px;">Applying Steering Profile</div>
    <div style="color:#aaa;font-size:13px;margin-bottom:18px;">
      <span id="prof-from">0.0</span>° &rarr; <span id="prof-to">0.0</span>°
    </div>
    <div style="background:#333;border-radius:6px;height:10px;margin-bottom:10px;overflow:hidden;">
      <div id="prof-bar" style="background:linear-gradient(90deg,#f84,#fa8);height:100%;width:0%;transition:width 0.1s linear;border-radius:6px;"></div>
    </div>
    <div style="color:#666;font-size:12px;"><span id="prof-elapsed">0.0</span> / <span id="prof-total">0.0</span> s</div>
  </div>
</div>

<div style="padding:10px 12px 0; display:flex; align-items:center; gap:16px;">
  <h2>Ackermann Config UI</h2>
  <span id="conn-badge" style="font-size:12px;padding:3px 8px;border-radius:10px;background:#333;color:#888;">not connected</span>
</div>

<div id="layout">
  <div class="card" id="viz-card">
    <h3>Bird's-eye view</h3>
    <svg id="robot-svg" width="400" height="460" viewBox="-200 -230 400 460" overflow="hidden">
      <!-- grid lines -->
      <line x1="-200" y1="0" x2="200" y2="0" stroke="#2a2a2a" stroke-width="1"/>
      <line x1="0" y1="-230" x2="0" y2="230" stroke="#2a2a2a" stroke-width="1"/>

      <!-- Wheelbase dimension line (right side) -->
      <line x1="92" y1="-55" x2="92" y2="55" stroke="#446" stroke-width="1" stroke-dasharray="3,2"/>
      <line x1="87" y1="-55" x2="97" y2="-55" stroke="#556" stroke-width="1.5"/>
      <line x1="87" y1="55"  x2="97" y2="55"  stroke="#556" stroke-width="1.5"/>
      <text id="dim-wb" x="100" y="3" font-size="9" fill="#668" text-anchor="start" dominant-baseline="middle">L=0.20m</text>

      <!-- Track width dimension line (bottom) -->
      <line x1="-65" y1="95" x2="65" y2="95" stroke="#446" stroke-width="1" stroke-dasharray="3,2"/>
      <line x1="-65" y1="90" x2="-65" y2="100" stroke="#556" stroke-width="1.5"/>
      <line x1="65"  y1="90" x2="65"  y2="100" stroke="#556" stroke-width="1.5"/>
      <text id="dim-tw" x="0" y="111" font-size="9" fill="#668" text-anchor="middle">W=0.15m</text>

      <!-- Ackermann arcs (updated by JS) -->
      <g id="ackermann-arcs"></g>

      <!-- Robot body -->
      <rect x="-40" y="-55" width="80" height="110" rx="6" fill="#1e2a3a" stroke="#4af" stroke-width="1.5"/>
      <polygon points="0,-70 -7,-55 7,-55" fill="#4af" opacity="0.7"/>
      <text x="0" y="-82" text-anchor="middle" font-size="10" fill="#4af">FWD</text>

      <!-- Wheels: rect + axis line (rotates with wheel) + label -->
      <g id="wheel-FL">
        <rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/>
        <line x1="0" y1="-20" x2="0" y2="20" stroke="#6af" stroke-width="1" opacity="0.8"/>
        <text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FL</text>
      </g>
      <g id="wheel-FR">
        <rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/>
        <line x1="0" y1="-20" x2="0" y2="20" stroke="#6af" stroke-width="1" opacity="0.8"/>
        <text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FR</text>
      </g>
      <g id="wheel-RL">
        <rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/>
        <line x1="0" y1="-20" x2="0" y2="20" stroke="#6af" stroke-width="1" opacity="0.8"/>
        <text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RL</text>
      </g>
      <g id="wheel-RR">
        <rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/>
        <line x1="0" y1="-20" x2="0" y2="20" stroke="#6af" stroke-width="1" opacity="0.8"/>
        <text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RR</text>
      </g>

      <!-- Steer angle labels (fixed world position, updated by JS) -->
      <text id="angle-FL" x="-65" y="-80" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
      <text id="angle-FR" x="65"  y="-80" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
      <text id="angle-RL" x="-65" y="78"  text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
      <text id="angle-RR" x="65"  y="78"  text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>

      <!-- Drive arrows and rotation centre -->
      <g id="arrow-FL"></g>
      <g id="arrow-FR"></g>
      <g id="arrow-RL"></g>
      <g id="arrow-RR"></g>
      <circle id="rc-dot" r="5" fill="#f80" opacity="0"/>
      <text id="rc-label" x="0" y="0" font-size="10" fill="#f80" opacity="0">RC</text>

      <!-- Legend: bottom-left corner of viewBox (-200,-230 to 200,230) -->
      <g transform="translate(-196, 150)" font-size="10" fill="#aaa">
        <rect x="-2" y="-2" width="152" height="76" rx="3" fill="#1a1a1a" stroke="#444" stroke-width="0.8" opacity="0.85"/>
        <text y="10"><tspan font-weight="bold" fill="#ddd">FL/FR</tspan>  Front Left / Right</text>
        <text y="22"><tspan font-weight="bold" fill="#ddd">RL/RR</tspan>  Rear Left / Right</text>
        <text y="34"><tspan font-weight="bold" fill="#ddd">L</tspan>  Wheelbase</text>
        <text y="46"><tspan font-weight="bold" fill="#ddd">W</tspan>  Track Width</text>
        <text y="58"><tspan font-weight="bold" fill="#ddd">FWD</tspan>  Forward direction</text>
        <text y="70"><tspan font-weight="bold" fill="#f80">RC</tspan>  Rotation Centre</text>
      </g>
    </svg>
    <div style="margin-top:8px;">
      <table>
        <tr><th>Wheel</th><th>DXL IDs</th><th>Steer angle</th><th>Steer tick</th><th>Drive</th><th>Raw</th></tr>
        <tbody id="wheel-table"></tbody>
      </table>
      <div style="margin-top:8px;color:#888;font-size:12px;">
        Turning radius: <span id="tr-val" style="color:#f80">-</span>
        &nbsp;|&nbsp; Clamped steer: <span id="cs-val" style="color:#adf">-</span> deg
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Drive command</h3>
    <div class="slider-row">
      <label>Steer (deg)</label>
      <input type="range" id="sl-steer" min="-30" max="30" step="0.5" value="0">
      <span class="val"><span id="lbl-steer">0.0</span> deg</span>
    </div>
    <div style="font-size:11px;color:#666;margin:-6px 0 8px 98px;">
      actual: <span id="fb-steer" style="color:#fa0">—</span>
    </div>
    <div class="slider-row">
      <label>Speed (m/s)</label>
      <input type="range" id="sl-speed" min="-0.5" max="0.5" step="0.01" value="0">
      <span class="val"><span id="lbl-speed">0.00</span></span>
    </div>
    <div style="font-size:11px;color:#666;margin:-6px 0 8px 98px;">
      actual: <span id="fb-speed" style="color:#fa0">—</span>
    </div>
    <div class="btn-row">
      <button class="btn btn-send"  onclick="sendCmd()">Send to robot</button>
      <button class="btn btn-estop" onclick="sendEstop()">E-STOP</button>
      <button id="btn-torque" class="btn btn-torque-on" onclick="toggleTorque()">Torque: ON</button>
    </div>
  </div>

  <div class="card" id="mapper-card">
    <h3>Servo ID Mapper</h3>
    <div style="font-size:12px;color:#888;margin-bottom:10px;">
      Click a servo ID to nudge it. Watch the robot, then click the role that moved.
      Repeat for all 8. The servo_ids field below is updated automatically.
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;" id="nudge-btns"></div>
    <div style="font-size:12px;color:#aaa;margin-bottom:6px;">
      Assign nudged servo (<span id="nudge-active-id" style="color:#fa0">none</span>) to role:
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;" id="role-btns"></div>
    <div style="margin-top:10px;font-size:12px;">
      <table style="width:100%;border-collapse:collapse;" id="map-table">
        <tr>
          <th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 4px;">Role</th>
          <th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 4px;">Assigned ID</th>
        </tr>
      </table>
    </div>
    <div class="btn-row" style="margin-top:8px;">
      <button class="btn btn-estop" onclick="stopNudge()" style="font-size:12px;padding:5px 12px;">Stop nudge</button>
      <button class="btn btn-save"  onclick="applyMap()"  style="font-size:12px;padding:5px 12px;">Apply to servo_ids</button>
      <button class="btn btn-load"  onclick="clearMap()"  style="font-size:12px;padding:5px 12px;">Clear map</button>
    </div>
  </div>

  <div class="card">
    <h3>AckermannConfig</h3>
    <div class="cfg-grid">
      <div class="cfg-row"><label>wheelbase (m)</label>           <input id="c-wheelbase" type="number" step="0.01" value="0.20"></div>
      <div class="cfg-row"><label>track_width (m)</label>         <input id="c-track"     type="number" step="0.01" value="0.15"></div>
      <div class="cfg-row"><label>max_steer_deg</label>           <input id="c-maxsteer"  type="number" step="1"    value="30"></div>
      <div class="cfg-row"><label>max_speed_mps</label>           <input id="c-maxspeed"  type="number" step="0.05" value="0.5"></div>
      <div class="cfg-row"><label>max_wheel_ticks</label>         <input id="c-maxticks"  type="number" step="10"   value="300"></div>
      <div class="cfg-row"><label>steer_center_ticks</label>      <input id="c-center"    type="number" step="1"    value="512"></div>
      <div class="cfg-row"><label>steer_dir [FL,FR,RL,RR]</label><input id="c-sdir" type="text" value="1,-1,-1,1"></div>
      <div class="cfg-row"><label>drive_dir [FL,FR,RL,RR]</label><input id="c-ddir" type="text" value="1,-1,1,-1"></div>
      <div class="cfg-row" style="grid-column:1/-1"><label>steer_offset_deg [FL,FR,RL,RR]</label><input id="c-offset" type="text" value="0,0,0,0" style="width:160px"></div>
      <div class="cfg-row" style="grid-column:1/-1">
        <label style="color:#f84;">servo_ids [FL_s,FR_s,RL_s,RR_s,<br>FL_d,FR_d,RL_d,RR_d]</label>
        <input id="c-sids" type="text" value="4,2,8,6,3,1,7,5" style="width:200px">
        <span style="font-size:11px;color:#888;margin-left:6px;">DXL IDs for each wheel role</span>
      </div>
      <div class="cfg-row"><label>steer_rate (°/s)</label>
        <input id="c-steer-rate"  type="number" step="5" min="1" max="180" value="30">
        <span style="font-size:11px;color:#888;margin-left:6px;">max steering rate during profile</span>
      </div>
      <div class="cfg-row"><label>steer_accel (°/s²)</label>
        <input id="c-steer-accel" type="number" step="10" min="1" max="360" value="60">
        <span style="font-size:11px;color:#888;margin-left:6px;">ramp acceleration during profile</span>
      </div>
    </div>

    <!-- Steer calibration -->
    <div style="margin-top:14px;border-top:1px solid #333;padding-top:12px;">
      <h3>Steer centre calibration</h3>
      <div style="font-size:12px;color:#888;margin-bottom:8px;">
        1. Click <b style="color:#fa0">Torque off steering</b> — drive wheels stop, steering goes limp.<br>
        2. Push each wheel physically to straight-ahead.<br>
        3. Click <b style="color:#4f4">Record neutral</b> — reads current ticks and fills steer_offset_deg.
      </div>
      <div class="btn-row">
        <button class="btn btn-estop"  onclick="steerTorqueOff()"  style="font-size:12px;padding:5px 12px;">Torque off steering</button>
        <button class="btn btn-send"   onclick="recordCenters()"   style="font-size:12px;padding:5px 12px;">Record neutral</button>
      </div>
      <div id="calib-result" style="margin-top:8px;font-size:12px;color:#888;"></div>
    </div>

    <!-- AX-12A position space bars -->
    <div style="margin-top:12px;">
      <h3>AX-12A position space (0 - 1023 ticks / 0 - 300 deg)</h3>
      <div id="pos-bars"></div>
    </div>

    <div class="btn-row">
      <button class="btn btn-save" onclick="saveConfig()">Save config</button>
      <button class="btn btn-load" onclick="loadConfig()">Load config</button>
    </div>
    <div style="margin-top:14px;">
      <h3>Robot state <span style="font-size:11px;color:#555;font-weight:normal">(auto-refreshes)</span></h3>
      <table>
        <tr><th>ID</th><th>Mode</th><th>Pos</th><th>Speed</th><th>Temp</th><th>Volt</th></tr>
        <tbody id="state-table"><tr><td colspan="6" style="color:#555;padding:6px;">-</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<div id="status-bar">Ready. Adjust sliders to preview - click "Send to robot" to drive.</div>

<script>
var WHEEL_POS = {
  FL: { x: -65, y: -55 }, FR: { x: 65, y: -55 },
  RL: { x: -65, y:  55 }, RR: { x: 65, y:  55 },
};
var WHEEL_ORDER = ['FL','FR','RL','RR'];

function readConfig() {
  function parseDir(id) {
    return document.getElementById(id).value.split(',').map(function(v){ return parseInt(v.trim()); });
  }
  function parseFloat4(id) {
    return document.getElementById(id).value.split(',').map(function(v){ return parseFloat(v.trim()); });
  }
  return {
    wheelbase:             parseFloat(document.getElementById('c-wheelbase').value),
    track_width:           parseFloat(document.getElementById('c-track').value),
    max_steer_deg:         parseFloat(document.getElementById('c-maxsteer').value),
    max_speed_mps:         parseFloat(document.getElementById('c-maxspeed').value),
    max_wheel_speed_ticks: parseInt(document.getElementById('c-maxticks').value),
    steer_center_ticks:    parseInt(document.getElementById('c-center').value),
    steer_dir:             parseDir('c-sdir'),
    drive_dir:             parseDir('c-ddir'),
    steer_offset_deg:      parseFloat4('c-offset'),
    servo_ids:             parseDir('c-sids'),
    steer_rate_deg_s:      parseFloat(document.getElementById('c-steer-rate').value)  || 30,
    steer_accel_deg_s2:    parseFloat(document.getElementById('c-steer-accel').value) || 60,
  };
}

function fillConfig(c) {
  document.getElementById('c-wheelbase').value = c.wheelbase;
  document.getElementById('c-track').value     = c.track_width;
  document.getElementById('c-maxsteer').value  = c.max_steer_deg;
  document.getElementById('c-maxspeed').value  = c.max_speed_mps;
  document.getElementById('c-maxticks').value  = c.max_wheel_speed_ticks;
  document.getElementById('c-center').value    = c.steer_center_ticks;
  document.getElementById('c-sdir').value      = c.steer_dir.join(',');
  document.getElementById('c-ddir').value      = c.drive_dir.join(',');
  var off = c.steer_offset_deg || [0,0,0,0];
  document.getElementById('c-offset').value    = off.join(',');
  var ids = c.servo_ids || [1,2,3,4,5,6,7,8];
  document.getElementById('c-sids').value      = ids.join(',');
  updateSliderRange(c.max_steer_deg, c.max_speed_mps);
}

function updateSliderRange(maxSteer, maxSpeed) {
  var sl = document.getElementById('sl-steer');
  sl.min = -maxSteer; sl.max = maxSteer;
  if (parseFloat(sl.value) > maxSteer)  sl.value =  maxSteer;
  if (parseFloat(sl.value) < -maxSteer) sl.value = -maxSteer;
  document.getElementById('lbl-steer').textContent = parseFloat(sl.value).toFixed(1);
  var ss = document.getElementById('sl-speed');
  ss.min = -maxSpeed; ss.max = maxSpeed;
  if (parseFloat(ss.value) > maxSpeed)  ss.value =  maxSpeed;
  if (parseFloat(ss.value) < -maxSpeed) ss.value = -maxSpeed;
  document.getElementById('lbl-speed').textContent = parseFloat(ss.value).toFixed(2);
}

var slSteer = document.getElementById('sl-steer');
var slSpeed = document.getElementById('sl-speed');

// ── Steer slider press / drag / release logic ─────────────────────────────────
// While the slider is held down  → live real-time control (drag case).
// On release after a large instant jump (no drag) → trapezoidal profile.
// Threshold: jumps > CLICK_JUMP_THRESHOLD degrees with no dragging steps trigger a profile.
var CLICK_JUMP_THRESHOLD = 3.0;
var _sliderHeld    = false;
var _pressSteer    = 0.0;   // _sentSteer at the moment the slider was pressed
var _hasDragged    = false;
var _pendingJump   = false;

function onSteerPress() {
  _sliderHeld  = true;
  _pressSteer  = _sentSteer;
  _hasDragged  = false;
  _pendingJump = false;
}
function onSteerRelease() {
  if (!_sliderHeld) return;
  _sliderHeld = false;
  if (_pendingJump) {
    _pendingJump = false;
    if (_driving && !_profiling) sendCmd();  // profile from _sentSteer to slider value
  } else {
    _sentSteer = parseFloat(slSteer.value);  // drag or tiny click — already live
  }
  _hasDragged = false;
}
slSteer.addEventListener('mousedown',  onSteerPress);
slSteer.addEventListener('touchstart', onSteerPress, {passive: true});
slSteer.addEventListener('mouseup',    onSteerRelease);
slSteer.addEventListener('touchend',   onSteerRelease);
document.addEventListener('mouseup',   onSteerRelease);  // catch release outside slider

slSteer.addEventListener('input', function() {
  document.getElementById('lbl-steer').textContent = parseFloat(slSteer.value).toFixed(1);
  preview();
  if (!_driving || _profiling) return;

  var delta = Math.abs(parseFloat(slSteer.value) - _pressSteer);
  if (!_hasDragged && !_pendingJump) {
    if (delta > CLICK_JUMP_THRESHOLD) {
      // Large instant jump — defer until mouseup confirms it's a click
      _pendingJump = true;
    } else {
      // Small step — start of a drag, send live
      _hasDragged = true;
      _sentSteer  = parseFloat(slSteer.value);
      doDrive();
    }
  } else if (_hasDragged) {
    // Continuing drag — send live
    _sentSteer = parseFloat(slSteer.value);
    doDrive();
  }
  // If _pendingJump, hold off sending until mouseup
});

slSpeed.addEventListener('input', function() {
  document.getElementById('lbl-speed').textContent = parseFloat(slSpeed.value).toFixed(2);
  driveIfActive();  // speed is fine to send immediately — no steering jerk
  preview();
});
document.querySelectorAll('.cfg-grid input').forEach(function(el){ el.addEventListener('input', function(){
  var ms = parseFloat(document.getElementById('c-maxsteer').value);
  var mp = parseFloat(document.getElementById('c-maxspeed').value);
  if (!isNaN(ms) && !isNaN(mp)) updateSliderRange(ms, mp);
  preview();
}); });

function postJSON(url, data, cb) {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', url);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() { try { cb(null, JSON.parse(xhr.responseText)); } catch(e){ cb(e); } };
  xhr.onerror = function() { cb(new Error('network error')); };
  xhr.send(JSON.stringify(data));
}

function getJSON(url, cb) {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', url);
  xhr.onload = function() { try { cb(null, JSON.parse(xhr.responseText)); } catch(e){ cb(e); } };
  xhr.onerror = function() { cb(new Error('network error')); };
  xhr.send();
}

function preview() {
  var body = { steer_deg: parseFloat(slSteer.value), speed_mps: parseFloat(slSpeed.value), config: readConfig() };
  postJSON('/compute', body, function(err, d) {
    if (err) { setStatus('Compute error: ' + err, true); return; }
    updateViz(d);
  });
}

var _driving      = false;
var _torqueOn     = true;
var _profiling      = false;
var _profStart      = 0;
var _profDuration   = 0;
var _profTargetSteer = 0.0;  // target steer stored at profile start — not read from DOM
var _sentSteer    = 0.0;   // steer the robot is currently at (updated after each profile or live drag)

// ── Profile splash ─────────────────────────────────────────────────────────────

function showProfileSplash(fromDeg, toDeg, durationMs) {
  _profiling       = true;
  _profStart       = Date.now();
  _profDuration    = durationMs;
  _profTargetSteer = toDeg;  // store as number — avoids DOM text parseFloat(0) falsy bug
  document.getElementById('prof-from').textContent    = fromDeg.toFixed(1);
  document.getElementById('prof-to').textContent      = toDeg.toFixed(1);
  document.getElementById('prof-total').textContent   = (durationMs / 1000).toFixed(1);
  document.getElementById('prof-elapsed').textContent = '0.0';
  document.getElementById('prof-bar').style.width     = '0%';
  var el = document.getElementById('profile-splash');
  el.style.display = 'flex';
  pollProfile();
}

function hideProfileSplash() {
  _profiling = false;
  document.getElementById('profile-splash').style.display = 'none';
}

function pollProfile() {
  if (!_profiling) return;
  var elapsed = Date.now() - _profStart;
  var pct = _profDuration > 0 ? Math.min(100, elapsed / _profDuration * 100) : 100;
  document.getElementById('prof-bar').style.width     = pct.toFixed(1) + '%';
  document.getElementById('prof-elapsed').textContent = (elapsed / 1000).toFixed(1);

  postJSON('/profile_status', {}, function(err, d) {
    if (err || (d && d.active)) {
      setTimeout(pollProfile, 100);
    } else {
      document.getElementById('prof-bar').style.width = '100%';
      hideProfileSplash();
      // Record the steer angle the robot has now reached
      _sentSteer = _profTargetSteer;  // use stored number, not DOM text (avoids 0.0 falsy bug)
      // Resume normal drive loop — watchdog uses _sentSteer, not raw slider
      _driving = true;
      doDrive();
    }
  });
}

// ── Drive loop ─────────────────────────────────────────────────────────────────

function sendCmd() {
  if (!_torqueOn) { setTorqueUI(true); }
  var steer_deg = parseFloat(slSteer.value);
  var speed_mps = parseFloat(slSpeed.value);
  var cfg       = readConfig();
  postJSON('/send_profile', { steer_deg: steer_deg, speed_mps: speed_mps, config: cfg },
    function(err, d) {
      if (err || (d && d.error)) {
        setStatus('Profile error: ' + (d && d.error ? d.error : err), true);
        return;
      }
      setStatus('Profile: ' + d.from_deg.toFixed(1) + '° → ' + d.to_deg.toFixed(1) +
                '°  (' + (d.duration_ms / 1000).toFixed(1) + ' s)');
      _driving = false;  // pause watchdog loop while profile runs
      showProfileSplash(d.from_deg, d.to_deg, d.duration_ms);
    }
  );
}

function doDrive() {
  if (!_driving) return;
  // Use _sentSteer (last profiled steer) — never jump raw slider value directly to robot
  var body = { steer_deg: _sentSteer, speed_mps: parseFloat(slSpeed.value), config: readConfig() };
  postJSON('/drive', body, function(err, d) {
    if (err || (d && d.error)) { setStatus('Robot: ' + (d && d.error ? d.error : err), true); return; }
    if (d && !d.profiling) {
      setStatus('Holding  steer=' + _sentSteer.toFixed(1) + '°  speed=' + body.speed_mps.toFixed(2) + ' m/s');
      updateViz(d);
    }
  });
}

function driveIfActive() {
  if (!_driving) return;
  doDrive();
}

// Feed the firmware watchdog at 10 Hz while driving (not during profile — profile thread feeds it)
setInterval(function() { if (_driving && !_profiling) doDrive(); }, 100);

function sendEstop() {
  _driving = false;
  _pendingJump = false;
  hideProfileSplash();
  postJSON('/estop', {}, function(err) {
    setStatus(err ? 'Estop error: ' + err : 'E-STOP sent.', !!err);
    if (!err) setTorqueUI(false);
  });
}

function toggleTorque() {
  var newState = !_torqueOn;
  if (_driving && !newState) _driving = false;
  postJSON('/torque', { on: newState, config: readConfig() }, function(err, d) {
    if (err || (d && d.error)) { setStatus('Torque error: ' + (d && d.error ? d.error : err), true); return; }
    setTorqueUI(newState);
    setStatus(newState ? 'Torque enabled (holding neutral).' : 'Torque disabled — robot limp.');
  });
}

function setTorqueUI(on) {
  _torqueOn = on;
  var btn = document.getElementById('btn-torque');
  btn.textContent = on ? 'Torque: ON' : 'Torque: OFF';
  btn.className = 'btn ' + (on ? 'btn-torque-on' : 'btn-torque-off');
}

function saveConfig() {
  postJSON('/config', readConfig(), function(err, d) {
    setStatus(err ? 'Save error: ' + err : 'Config saved to ' + d.saved, !!err);
  });
}

function loadConfig() {
  getJSON('/config', function(err, d) {
    if (err) { setStatus('Load error: ' + err, true); return; }
    fillConfig(d);
    setStatus('Config loaded.');
    preview();
  });
}

function updateViz(data) {
  var wheels = data.wheels;
  if (data.position_space) updatePosBars(data.position_space);
  var cfg = readConfig();
  var sids = cfg.servo_ids || [1,2,3,4,5,6,7,8];
  var tbody = document.getElementById('wheel-table');
  tbody.innerHTML = '';
  wheels.forEach(function(w, i) {
    var color = w.drive_dir === 'CCW' ? '#4f4' : w.drive_dir === 'CW' ? '#f84' : '#888';
    var sign  = w.steer_angle >= 0 ? '+' : '';
    var steerId = sids[i] !== undefined ? sids[i] : (i+1);
    var driveId = sids[i+4] !== undefined ? sids[i+4] : (i+5);
    tbody.innerHTML +=
      '<tr><td class="hl">' + w.label + '</td>' +
      '<td style="color:#888;font-size:11px;">S:ID' + steerId + ' D:ID' + driveId + '</td>' +
      '<td>' + sign + w.steer_angle.toFixed(1) + ' deg</td>' +
      '<td>' + w.steer_tick + '</td>' +
      '<td style="color:' + color + '">' + w.drive_dir + ' ' + w.drive_mag + '</td>' +
      '<td style="color:#555">' + w.drive_raw + '</td></tr>';
  });

  document.getElementById('tr-val').textContent =
    data.turning_radius !== null ? data.turning_radius + ' m' : 'inf (straight)';
  var cs = data.steer_clamped;
  document.getElementById('cs-val').textContent = (cs >= 0 ? '+' : '') + cs.toFixed(1);

  updateAckermannArcs(data.steer_clamped, cfg);

  // Update angle labels and dimension labels
  var angleLabels = ['FL','FR','RL','RR'];
  angleLabels.forEach(function(lbl, i) {
    var el = document.getElementById('angle-' + lbl);
    if (el) {
      var a = wheels[i].steer_angle;
      el.textContent = (a >= 0 ? '+' : '') + a.toFixed(1) + '°';
    }
  });
  var cfg = readConfig();
  var dimWB = document.getElementById('dim-wb');
  if (dimWB) dimWB.textContent = 'L=' + (cfg.wheelbase || 0.20).toFixed(3) + 'm';
  var dimTW = document.getElementById('dim-tw');
  if (dimTW) dimTW.textContent = 'W=' + (cfg.track_width || 0.15).toFixed(3) + 'm';

  WHEEL_ORDER.forEach(function(lbl, i) {
    var w   = wheels[i];
    var pos = WHEEL_POS[lbl];
    document.getElementById('wheel-' + lbl).setAttribute(
      'transform', 'translate(' + pos.x + ',' + pos.y + ') rotate(' + w.steer_angle + ')');

    var arr = document.getElementById('arrow-' + lbl);
    arr.innerHTML = '';
    if (w.drive_mag > 0) {
      var len   = Math.min(w.drive_mag / 300, 1) * 28 + 8;
      var dir   = w.drive_dir === 'CCW' ? -1 : 1;
      var y2    = dir * len;
      var tipY  = y2 + dir * 4;
      var color = w.drive_dir === 'CCW' ? '#4f4' : '#f84';
      arr.innerHTML =
        '<g transform="translate(' + pos.x + ',' + pos.y + ') rotate(' + w.steer_angle + ')">' +
        '<line x1="0" y1="0" x2="0" y2="' + y2 + '" stroke="' + color + '" stroke-width="2"/>' +
        '<polygon points="0,' + tipY + ' -3,' + y2 + ' 3,' + y2 + '" fill="' + color + '"/>' +
        '</g>';
    }
  });

  var rc    = document.getElementById('rc-dot');
  var rcLbl = document.getElementById('rc-label');
  if (data.turning_radius !== null) {
    var sign = data.steer_clamped >= 0 ? 1 : -1;
    var cfg2 = readConfig();
    var W2px = 65;  // SVG x-position of wheel centres (px)
    var W2m  = (cfg2.track_width || 0.15) / 2;
    var scaleX = W2px / W2m;
    var rcX  = sign * data.turning_radius * scaleX;
    rc.setAttribute('cx', rcX); rc.setAttribute('cy', 0); rc.setAttribute('opacity', 0.8);
    rcLbl.setAttribute('x', rcX + 7); rcLbl.setAttribute('y', 4); rcLbl.setAttribute('opacity', 0.8);
  } else {
    rc.setAttribute('opacity', 0); rcLbl.setAttribute('opacity', 0);
  }
}

function updatePosBars(posSpace) {
  var labels = ['FL','FR','RL','RR'];
  var W = 280; // bar width in px
  var html = '<table style="width:100%;border-collapse:collapse;margin-top:4px;">';
  html += '<tr><th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 4px;font-size:11px;">Wheel</th>' +
          '<th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 4px;font-size:11px;">Position space</th>' +
          '<th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 4px;font-size:11px;">Ticks</th></tr>';
  posSpace.forEach(function(p, i) {
    var neutralPx  = Math.round(p.neutral_tick  / 1023 * W);
    var minPx      = Math.round(p.min_tick      / 1023 * W);
    var maxPx      = Math.round(p.max_tick      / 1023 * W);
    var currentPx  = Math.round(p.current_tick  / 1023 * W);
    var rangePx    = maxPx - minPx;
    html +=
      '<tr><td style="padding:4px 4px;color:#7fc;font-weight:bold;">' + labels[i] + '</td>' +
      '<td style="padding:4px;">' +
        '<div style="position:relative;width:' + W + 'px;height:14px;background:#222;border-radius:3px;border:1px solid #444;">' +
          // usable range (blue)
          '<div style="position:absolute;left:' + minPx + 'px;width:' + rangePx + 'px;height:100%;background:#1a3a5a;border-radius:2px;"></div>' +
          // neutral marker (white line)
          '<div style="position:absolute;left:' + neutralPx + 'px;width:2px;height:100%;background:#fff;opacity:0.5;"></div>' +
          // current tick marker (yellow)
          '<div style="position:absolute;left:' + (currentPx - 2) + 'px;width:4px;height:100%;background:#fa0;border-radius:2px;"></div>' +
        '</div>' +
      '</td>' +
      '<td style="padding:4px;font-size:11px;color:#888;">' +
        'neutral=' + p.neutral_tick + ' range=[' + p.min_tick + ',' + p.max_tick + '] now=' +
        '<span style="color:#fa0">' + p.current_tick + '</span>' +
      '</td></tr>';
  });
  html += '</table>' +
    '<div style="margin-top:4px;font-size:11px;color:#555;">' +
    '<span style="display:inline-block;width:12px;height:8px;background:#1a3a5a;border:1px solid #444;vertical-align:middle;"></span> usable range &nbsp;' +
    '<span style="display:inline-block;width:3px;height:10px;background:#fff;opacity:0.5;vertical-align:middle;"></span> neutral &nbsp;' +
    '<span style="display:inline-block;width:6px;height:10px;background:#fa0;border-radius:1px;vertical-align:middle;"></span> current' +
    '</div>';
  document.getElementById('pos-bars').innerHTML = html;
}

function refreshState() {
  getJSON('/state', function(err, d) {
    if (err) return;
    var badge = document.getElementById('conn-badge');
    if (!d.connected) {
      badge.textContent = 'not connected';
      badge.style.background = '#333'; badge.style.color = '#888'; return;
    }
    badge.textContent = 'connected';
    badge.style.background = '#1a3a1a'; badge.style.color = '#4f4';

    // Update actual-value feedback labels only — sliders are user-controlled
    if (d.feedback) {
      document.getElementById('fb-steer').textContent = d.feedback.steer_deg.toFixed(1) + ' deg';
      document.getElementById('fb-speed').textContent = d.feedback.speed_mps.toFixed(3) + ' m/s';
    }

    var tbody = document.getElementById('state-table');
    if (!d.state) { tbody.innerHTML = '<tr><td colspan="6" style="color:#555">waiting...</td></tr>'; return; }
    tbody.innerHTML = d.state.servos.map(function(s) {
      if (!s.available) return '<tr><td>' + s.id + '</td><td colspan="5" style="color:#555">UNAVAIL</td></tr>';
      return '<tr><td>' + s.id + '</td><td>' + s.mode + '</td><td>' + s.pos + '</td>' +
             '<td>' + s.speed + '</td><td>' + s.temp_c + 'C</td><td>' + s.volt_v.toFixed(1) + 'V</td></tr>';
    }).join('');
    if (d.state.e_stop) { setStatus('E-STOP active on robot', true); setTorqueUI(false); }
  });
}

function setStatus(msg, err) {
  var el = document.getElementById('status-bar');
  el.textContent = msg;
  el.className = err ? 'err' : '';
}

// ── Ackermann arcs ───────────────────────────────────────────────────────────
function updateAckermannArcs(steer, cfg) {
  var el = document.getElementById('ackermann-arcs');
  if (!el) return;
  if (Math.abs(steer) < 0.5) { el.innerHTML = ''; return; }

  var L2  = cfg.wheelbase   / 2;
  var W2  = cfg.track_width / 2;
  var sign = steer > 0 ? 1 : -1;
  var dRad = Math.abs(steer) * Math.PI / 180;
  var R    = L2 / Math.tan(dRad);

  // IRC x in SVG: scale lateral distance so W2 maps to 65 px (wheel x position)
  var scaleX = 65 / W2;
  var irc_x  = sign * R * scaleX;

  // Arc radii: defined to pass exactly through the wheel SVG positions
  var dxOut   = irc_x + sign * 65;
  var dxIn    = irc_x - sign * 65;
  var rOuter  = Math.sqrt(dxOut * dxOut + 55 * 55);
  var rInner  = Math.sqrt(dxIn  * dxIn  + 55 * 55);
  var rCenter = Math.abs(irc_x);  // vehicle centre turning radius

  // Arc sweeping exactly steer_deg around the IRC from vehicle centre (0,0).
  var angleStartDeg = sign > 0 ? 180 : 0;
  var angleEndDeg   = angleStartDeg + sign * Math.abs(steer);
  var angleEndRad   = angleEndDeg * Math.PI / 180;
  var xEnd = irc_x + rCenter * Math.cos(angleEndRad);
  var yEnd =          rCenter * Math.sin(angleEndRad);
  var sweepFlag = sign > 0 ? 1 : 0;
  var largeArc  = Math.abs(steer) > 180 ? 1 : 0;

  // Tangent direction at arc end via small step in direction of motion
  var stepRad = sign * 3 * Math.PI / 180;
  var tx = irc_x + rCenter * Math.cos(angleEndRad + stepRad) - xEnd;
  var ty =          rCenter * Math.sin(angleEndRad + stepRad) - yEnd;
  var tLen = Math.sqrt(tx*tx + ty*ty);
  if (tLen > 0) { tx /= tLen; ty /= tLen; }

  // Constant-length tangent arrow at arc end
  var arrowLen = 36;
  var tipX = xEnd + arrowLen * tx;
  var tipY = yEnd + arrowLen * ty;
  var aw = 5, al = 10;
  var arrowPath = 'M' + tipX.toFixed(1) + ',' + tipY.toFixed(1) +
    ' L' + (tipX - al*tx + aw*ty).toFixed(1) + ',' + (tipY - al*ty - aw*tx).toFixed(1) +
    ' L' + (tipX - al*tx - aw*ty).toFixed(1) + ',' + (tipY - al*ty + aw*tx).toFixed(1) + 'Z';

  // Label just beyond the arrow tip, continuing in the tangent direction
  var xLabel = tipX + 14 * tx;
  var yLabel = tipY + 14 * ty;

  el.innerHTML =
    // Outer wheel path circle
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rOuter.toFixed(1) +
      '" fill="none" stroke="#2a5a8a" stroke-width="1.2" stroke-dasharray="6,4" opacity="0.6"/>' +
    // Centre (vehicle body) path circle
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rCenter.toFixed(1) +
      '" fill="none" stroke="#3a7aaa" stroke-width="1.2" stroke-dasharray="3,4" opacity="0.6"/>' +
    // Inner wheel path circle
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rInner.toFixed(1) +
      '" fill="none" stroke="#2a5a8a" stroke-width="1.2" stroke-dasharray="6,4" opacity="0.6"/>' +
    // Arc sweeping steer_deg
    '<path d="M0,0 A' + rCenter.toFixed(1) + ',' + rCenter.toFixed(1) +
      ' 0 ' + largeArc + ',' + sweepFlag + ' ' + xEnd.toFixed(1) + ',' + yEnd.toFixed(1) +
      '" fill="none" stroke="#fa8" stroke-width="2" opacity="0.85"/>' +
    // Constant-length tangent arrow at arc end
    '<line x1="' + xEnd.toFixed(1) + '" y1="' + yEnd.toFixed(1) +
      '" x2="' + tipX.toFixed(1) + '" y2="' + tipY.toFixed(1) +
      '" stroke="#fa8" stroke-width="2" opacity="0.85"/>' +
    '<path d="' + arrowPath + '" fill="#fa8" opacity="0.85"/>' +
    // Steer angle label
    '<text x="' + xLabel.toFixed(1) + '" y="' + yLabel.toFixed(1) +
      '" text-anchor="middle" dominant-baseline="middle" font-size="11" fill="#fa8" ' +
      'style="text-shadow:0 0 4px #111">' +
      (steer >= 0 ? '+' : '') + steer.toFixed(1) + '°</text>';
}

// ── Steer centre calibration ─────────────────────────────────────────────────
function steerTorqueOff() {
  // Stop drive wheels (speed=0) and disable steering torque so user can push by hand.
  // Easiest: send estop (all torque off) then immediately re-enable drive at speed=0.
  _driving = false;
  postJSON('/estop', {}, function(err) {
    if (err) { setStatus('Estop error: ' + err, true); return; }
    setTorqueUI(false);
    setStatus('Steering torque off — push wheels to straight-ahead, then click Record neutral.');
  });
}

function recordCenters() {
  postJSON('/record_centers', { config: readConfig() }, function(err, d) {
    if (err || (d && d.error)) {
      setStatus('Record error: ' + (d && d.error ? d.error : err), true);
      return;
    }
    var offsets = d.steer_offset_deg;
    document.getElementById('c-offset').value = offsets.join(',');

    var labels = ['FL','FR','RL','RR'];
    var html = '<table style="border-collapse:collapse;">' +
      '<tr><th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 6px;">Wheel</th>' +
      '<th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 6px;">Servo ID</th>' +
      '<th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 6px;">Recorded tick</th>' +
      '<th style="color:#7cf;font-weight:normal;text-align:left;padding:2px 6px;">Offset (deg)</th></tr>';
    d.details.forEach(function(det, i) {
      var col = Math.abs(det.offset_deg) > 5 ? '#f84' : '#4f4';
      html += '<tr>' +
        '<td style="padding:2px 6px;color:#adf;">' + labels[i] + '</td>' +
        '<td style="padding:2px 6px;">' + det.id + '</td>' +
        '<td style="padding:2px 6px;">' + (det.pos !== null ? det.pos : 'UNAVAIL') + '</td>' +
        '<td style="padding:2px 6px;color:' + col + ';">' + det.offset_deg + '°</td></tr>';
    });
    html += '</table><div style="margin-top:4px;color:#aaa;">steer_offset_deg filled → click <b>Save config</b> to persist.</div>';
    document.getElementById('calib-result').innerHTML = html;
    setStatus('Neutral recorded. steer_offset_deg = [' + offsets.join(', ') + ']');
    preview();
  });
}

loadConfig();
setInterval(refreshState, 1000);

// ── Servo ID Mapper ──────────────────────────────────────────────────────────
var ROLES = ['FL_steer','FR_steer','RL_steer','RR_steer','FL_drive','FR_drive','RL_drive','RR_drive'];
var _nudgeId    = null;   // currently nudging servo ID
var _nudgeTimer = null;   // setInterval handle
var _roleMap    = {};     // role → servo_id

(function initMapper() {
  // Servo ID buttons (1-8)
  var nb = document.getElementById('nudge-btns');
  for (var i = 1; i <= 8; i++) {
    (function(sid) {
      var b = document.createElement('button');
      b.id = 'nudge-btn-' + sid;
      b.textContent = 'ID ' + sid;
      b.className = 'btn btn-load';
      b.style.cssText = 'font-size:12px;padding:5px 10px;';
      b.onclick = function() { startNudge(sid); };
      nb.appendChild(b);
    })(i);
  }

  // Role buttons
  var rb = document.getElementById('role-btns');
  ROLES.forEach(function(role) {
    var b = document.createElement('button');
    b.id = 'role-btn-' + role;
    b.textContent = role;
    b.className = 'btn btn-load';
    b.style.cssText = 'font-size:11px;padding:4px 8px;';
    b.onclick = function() { assignRole(role); };
    rb.appendChild(b);
  });

  renderMapTable();
})();

function startNudge(sid) {
  stopNudge();
  _nudgeId = sid;
  document.getElementById('nudge-active-id').textContent = 'ID ' + sid;

  // Highlight active nudge button
  for (var i = 1; i <= 8; i++) {
    var b = document.getElementById('nudge-btn-' + i);
    b.className = 'btn ' + (i === sid ? 'btn-send' : 'btn-load');
  }

  var cfg = readConfig();
  function doNudge() {
    postJSON('/nudge', {
      servo_id: _nudgeId,
      nudge_ticks: 40,
      center_ticks: cfg.steer_center_ticks || 512
    }, function(){});
  }
  doNudge();
  _nudgeTimer = setInterval(doNudge, 100);
  setStatus('Nudging ID ' + sid + ' — watch the robot, then click the role that moved.');
}

function stopNudge() {
  if (_nudgeTimer) { clearInterval(_nudgeTimer); _nudgeTimer = null; }
  _nudgeId = null;
  document.getElementById('nudge-active-id').textContent = 'none';
  for (var i = 1; i <= 8; i++) {
    var b = document.getElementById('nudge-btn-' + i);
    if (b) b.className = 'btn btn-load';
  }
  postJSON('/estop', {}, function(){});
}

function assignRole(role) {
  if (_nudgeId === null) { setStatus('Click a servo ID first to nudge it.', true); return; }
  _roleMap[role] = _nudgeId;
  stopNudge();
  renderMapTable();
  setStatus('Assigned ID ' + _roleMap[role] + ' → ' + role + '. Click next servo to nudge.');
}

function renderMapTable() {
  var tbody = '';
  ROLES.forEach(function(role) {
    var assigned = _roleMap[role] !== undefined ? 'ID ' + _roleMap[role] : '<span style="color:#555">—</span>';
    var roleColor = role.indexOf('steer') >= 0 ? '#adf' : '#afd';
    tbody += '<tr><td style="padding:2px 4px;color:' + roleColor + ';">' + role + '</td>' +
             '<td style="padding:2px 4px;color:#fa0;">' + assigned + '</td></tr>';
  });
  // Preserve header row, rebuild body rows
  var tbl = document.getElementById('map-table');
  var rows = tbl.querySelectorAll('tr');
  // Remove old data rows
  for (var i = rows.length - 1; i >= 1; i--) tbl.removeChild(rows[i]);
  // Add new rows
  var tmp = document.createElement('tbody');
  tmp.innerHTML = tbody;
  while (tmp.firstChild) tbl.appendChild(tmp.firstChild);

  // Dim role buttons that are already assigned
  ROLES.forEach(function(role) {
    var b = document.getElementById('role-btn-' + role);
    if (b) b.style.opacity = _roleMap[role] !== undefined ? '0.4' : '1.0';
  });
}

function applyMap() {
  var ids = ROLES.map(function(role) { return _roleMap[role] || 0; });
  document.getElementById('c-sids').value = ids.join(',');
  setStatus('servo_ids updated: [' + ids.join(', ') + ']. Click Save config to persist.');
}

function clearMap() {
  _roleMap = {};
  stopNudge();
  renderMapTable();
  setStatus('Mapper cleared.');
}
</script>
</body>
</html>"""


# ── Keepalive ─────────────────────────────────────────────────────────────────

def _keepalive_loop():
    """
    Sends torque-off CMD frames at 5 Hz when the UI is not actively driving.
    This keeps the firmware in binary mode and STATE frames flowing so the
    state table and steer calibration always have fresh data.
    When /drive is active (stamped within the last 0.3s) we stay silent and
    let the JS 10Hz loop own the bus.
    """
    while True:
        time.sleep(0.2)
        if _driver is None:
            continue
        if time.monotonic() - _last_drive_t > 0.3:
            _driver.send_estop()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _driver

    parser = argparse.ArgumentParser()
    parser.add_argument('--port',    '-p', default=None)
    parser.add_argument('--baud',    '-b', type=int, default=115200)
    parser.add_argument('--ui-port', '-u', type=int, default=8081)
    args = parser.parse_args()

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
        # Send one frame immediately to kick firmware into binary mode
        _driver.send_estop()
        # Start keepalive so STATE frames flow even when UI is idle
        t = threading.Thread(target=_keepalive_loop, daemon=True, name='keepalive')
        t.start()
        print('Robot connected.')
    else:
        print('No serial port found - running in simulation mode (no robot).')

    print('Open:  http://localhost:{}'.format(args.ui_port))
    server = HTTPServer(('0.0.0.0', args.ui_port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        if _driver:
            _driver.send_estop()
            _driver.stop()
            _driver.close()


if __name__ == '__main__':
    main()

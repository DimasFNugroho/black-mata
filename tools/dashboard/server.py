#!/usr/bin/env python3
"""
dashboard/server.py - Simple WASD keyboard drive dashboard.

Run:
    python3 tools/dashboard/server.py
    python3 tools/dashboard/server.py --port /dev/ttyACM0 --ui-port 8082

Then open:  http://<jetson-ip>:8082
"""

import argparse
import glob
import json
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.robot.serial_driver import SerialDriver
from software.robot.ackermann import Ackermann, AckermannConfig

_driver       = None
_last_drive_t = 0.0

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
}

CONFIG_PATH = Path(__file__).parents[1] / 'ackermann_ui' / 'ackermann_config.json'


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


_camera_url = 'http://localhost:8083/stream'


def _build_html(camera_url):
    return HTML_TEMPLATE.replace('__CAMERA_URL__', camera_url)


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

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
            body = _build_html(_camera_url).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == '/camera':
            try:
                req = urllib.request.urlopen(_camera_url, timeout=5)
                self.send_response(200)
                self.send_header('Content-Type', req.headers.get('Content-Type', 'multipart/x-mixed-replace; boundary=frame'))
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
            self._send_json({
                'connected': True,
                'state': {'seq': s.seq, 'e_stop': s.e_stop},
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


# ── HTML dashboard ─────────────────────────────────────────────────────────────
# __CAMERA_URL__ is substituted at request time via _build_html()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Drive Dashboard - Black-Mata</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: monospace;
  background: #0d0d0d;
  color: #ccc;
  font-size: 14px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── top bar ── */
#topbar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 16px;
  background: #111; border-bottom: 1px solid #222;
  flex-shrink: 0;
}
h1 { color: #7cf; font-size: 16px; letter-spacing: 2px; }
#conn-badge {
  font-size: 11px; padding: 2px 9px; border-radius: 10px;
  background: #222; color: #666;
}
#conn-badge.ok  { background: #0d2a0d; color: #4f4; }
#conn-badge.err { background: #2a0d0d; color: #f44; }
#status {
  margin-left: auto; font-size: 12px; color: #555;
}
#status.driving { color: #5fa; }
#status.err     { color: #f66; }

/* ── camera ── */
#cam-wrap {
  flex: 1 1 auto;
  display: flex; align-items: center; justify-content: center;
  background: #050505;
  overflow: hidden;
  min-height: 0;
}
#cam-img {
  width: 100%; height: 100%;
  display: block; object-fit: contain;
  transform: rotate(180deg);
}
#cam-placeholder {
  color: #333; font-size: 13px; text-align: center; line-height: 2;
}

/* ── controls bar (bottom) ── */
#controls {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  gap: 32px; flex-wrap: wrap;
  padding: 12px 20px;
  background: #111; border-top: 1px solid #222;
}

/* WASD key grid */
.wasd-grid { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.wasd-row  { display: flex; gap: 5px; }

.kb-key {
  width: 48px; height: 48px;
  border: 2px solid #333; border-radius: 8px;
  background: #1a1a1a; color: #555;
  display: flex; align-items: center; justify-content: center;
  font-size: 17px; font-weight: bold;
  user-select: none;
  transition: background 0.05s, color 0.05s, border-color 0.05s, box-shadow 0.05s;
}
.kb-key .sub { font-size: 8px; color: #444; margin-top: 2px; text-align: center; line-height: 1; }
.kb-key-inner { display: flex; flex-direction: column; align-items: center; }
.kb-key.active { background: #1a5a2a; color: #5fe; border-color: #4fa; box-shadow: 0 0 10px #2a8a4a88; }
.kb-key.active .sub { color: #5fa; }

/* gauges */
.gauges { display: flex; flex-direction: column; gap: 10px; }
.gauge-wrap { display: flex; flex-direction: column; gap: 3px; }
.gauge-label { font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 1px; }
.gauge-bar-track {
  width: 220px; height: 20px;
  background: #161616; border: 1px solid #2a2a2a; border-radius: 4px;
  position: relative; overflow: hidden;
}
.gauge-bar-fill {
  position: absolute; top: 0; height: 100%; border-radius: 3px;
  transition: left 0.08s, width 0.08s, background 0.08s;
}
.gauge-center-line {
  position: absolute; left: 50%; top: 0; width: 1px; height: 100%; background: #333;
}
.gauge-val { font-size: 15px; font-weight: bold; color: #7cf; text-align: center; }

/* hint + estop */
.hint-table { font-size: 10px; color: #444; border-collapse: collapse; }
.hint-table td { padding: 2px 6px; }
.hint-table .k { color: #7cf; font-weight: bold; }
.btn-estop {
  padding: 10px 22px; border: none; border-radius: 6px;
  background: #7a1515; color: #fff;
  font-family: monospace; font-size: 13px; font-weight: bold;
  cursor: pointer; letter-spacing: 1px;
  transition: background 0.1s;
}
.btn-estop:hover  { background: #c22; }
.btn-estop:active { background: #f33; }
</style>
</head>
<body>

<!-- top bar -->
<div id="topbar">
  <h1>DRIVE DASHBOARD</h1>
  <span id="conn-badge">connecting...</span>
  <span id="status">Click page, then use WASD to drive.</span>
  <span id="cfg-info" style="margin-left:auto;font-size:11px;color:#444;">loading config...</span>
</div>

<!-- camera stream -->
<div id="cam-wrap">
  <img id="cam-img" src="/camera"
       onerror="this.style.display='none';document.getElementById('cam-placeholder').style.display='block'">
  <div id="cam-placeholder" style="display:none;">
    No camera stream<br>
    <span style="color:#222;font-size:11px;">__CAMERA_URL__</span>
  </div>
</div>

<!-- controls bar -->
<div id="controls">

  <!-- WASD -->
  <div class="wasd-grid">
    <div class="wasd-row">
      <div id="kb-w" class="kb-key"><div class="kb-key-inner">W<span class="sub">FWD</span></div></div>
    </div>
    <div class="wasd-row">
      <div id="kb-a" class="kb-key"><div class="kb-key-inner">A<span class="sub">LEFT</span></div></div>
      <div id="kb-s" class="kb-key"><div class="kb-key-inner">S<span class="sub">REV</span></div></div>
      <div id="kb-d" class="kb-key"><div class="kb-key-inner">D<span class="sub">RIGHT</span></div></div>
    </div>
  </div>

  <!-- Gauges -->
  <div class="gauges">
    <div class="gauge-wrap">
      <div class="gauge-label">Steer</div>
      <div class="gauge-bar-track">
        <div id="gauge-steer" class="gauge-bar-fill" style="left:50%;width:0%;background:#7cf;"></div>
        <div class="gauge-center-line"></div>
      </div>
      <div id="val-steer" class="gauge-val">0.0 °</div>
    </div>
    <div class="gauge-wrap">
      <div class="gauge-label">Output %</div>
      <div class="gauge-bar-track">
        <div id="gauge-speed" class="gauge-bar-fill" style="left:50%;width:0%;background:#4fa;"></div>
        <div class="gauge-center-line"></div>
      </div>
      <div id="val-speed" class="gauge-val">0.00 m/s</div>
    </div>
  </div>

  <!-- Hint + E-STOP -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:10px;">
    <table class="hint-table">
      <tr><td class="k">W / S</td><td>+100% / −100% output</td></tr>
      <tr><td class="k">A / D</td><td>Steer Left / Right</td></tr>
      <tr><td class="k">Space / Esc</td><td>E-STOP</td></tr>
    </table>
    <button class="btn-estop" onclick="doEstop()">&#9632; E-STOP</button>
  </div>

</div>

<script>
var _keys     = { w: false, a: false, s: false, d: false };
var _maxSteer = 30.0;
var _maxTicks = 300;   // max_wheel_speed_ticks from config (0-1023)
var _driving  = false;

function loadConfig() {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/config');
  xhr.onload = function() {
    try {
      var c = JSON.parse(xhr.responseText);
      if (c.max_steer_deg)         _maxSteer = parseFloat(c.max_steer_deg);
      if (c.max_wheel_speed_ticks) _maxTicks = Math.min(parseInt(c.max_wheel_speed_ticks), 1023);
      var pct = (_maxTicks / 1023 * 100).toFixed(1);
      document.getElementById('cfg-info').textContent =
        'max output: ' + _maxTicks + '/1023 (' + pct + '%)  |  max steer: ' + _maxSteer + '°';
    } catch(e) {}
  };
  xhr.send();
}
loadConfig();
setInterval(loadConfig, 5000);  // refresh every 5s to pick up ackermann_ui saves

document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  var k = e.key.toLowerCase();
  if (k === 'w' || k === 'a' || k === 's' || k === 'd') {
    e.preventDefault();
    _keys[k] = true;
    updateKeyDisplay();
  } else if (k === ' ' || k === 'escape') {
    e.preventDefault();
    doEstop();
  }
});

document.addEventListener('keyup', function(e) {
  var k = e.key.toLowerCase();
  if (k === 'w' || k === 'a' || k === 's' || k === 'd') {
    _keys[k] = false;
    updateKeyDisplay();
  }
});

function updateKeyDisplay() {
  ['w','a','s','d'].forEach(function(k) {
    var el = document.getElementById('kb-' + k);
    if (_keys[k]) el.classList.add('active');
    else          el.classList.remove('active');
  });
}

setInterval(function() {
  var anyKey = _keys.w || _keys.a || _keys.s || _keys.d;
  var steer = 0, speed = 0;
  if (_keys.a) steer -= _maxSteer;
  if (_keys.d) steer += _maxSteer;
  if (_keys.w) speed += 1.0;   // +1.0 = full forward output
  if (_keys.s) speed -= 1.0;   // -1.0 = full reverse output

  updateGauges(steer, speed);

  if (anyKey) {
    _driving = true;
    sendDrive(steer, speed);
  } else if (_driving) {
    _driving = false;
    sendDrive(0, 0);
    setStatus('Stopped.', '');
  }
}, 100);

function sendDrive(steer, speed) {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/drive');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() {
    try {
      var d = JSON.parse(xhr.responseText);
      if (d.error) setStatus(d.error, 'err');
      else if (_driving) setStatus(
        'steer ' + (steer >= 0 ? '+' : '') + steer.toFixed(1) + '°  ' +
        'output ' + (speed >= 0 ? '+' : '') + Math.round(speed * 100) + '%', 'driving');
    } catch(e) {}
  };
  xhr.onerror = function() { setStatus('Network error', 'err'); };
  xhr.send(JSON.stringify({ steer_deg: steer, speed_mps: speed }));
}

function doEstop() {
  _driving = false;
  _keys = { w: false, a: false, s: false, d: false };
  updateKeyDisplay();
  updateGauges(0, 0);
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/estop');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() { setStatus('E-STOP sent.', 'err'); };
  xhr.send('{}');
}

function updateGauges(steer, speed) {
  var steerPct = (steer / _maxSteer) * 50;
  var gSteer = document.getElementById('gauge-steer');
  if (steerPct >= 0) {
    gSteer.style.left = '50%'; gSteer.style.width = steerPct + '%';
    gSteer.style.background = '#7cf';
  } else {
    gSteer.style.left = (50 + steerPct) + '%'; gSteer.style.width = (-steerPct) + '%';
    gSteer.style.background = '#fa8';
  }
  document.getElementById('val-steer').textContent =
    (steer >= 0 ? '+' : '') + steer.toFixed(1) + ' °';

  var speedPct = speed * 50;   // speed is -1.0…+1.0, maps to -50%…+50% of bar
  var gSpeed = document.getElementById('gauge-speed');
  if (speedPct >= 0) {
    gSpeed.style.left = '50%'; gSpeed.style.width = speedPct + '%';
    gSpeed.style.background = '#4fa';
  } else {
    gSpeed.style.left = (50 + speedPct) + '%'; gSpeed.style.width = (-speedPct) + '%';
    gSpeed.style.background = '#f84';
  }
  document.getElementById('val-speed').textContent =
    (speed >= 0 ? '+' : '') + Math.round(speed * 100) + '%';
}

function setStatus(msg, cls) {
  var el = document.getElementById('status');
  el.textContent = msg;
  el.className = cls || '';
}

setInterval(function() {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/state');
  xhr.onload = function() {
    try {
      var d = JSON.parse(xhr.responseText);
      var badge = document.getElementById('conn-badge');
      if (d.connected) {
        badge.textContent = 'connected'; badge.className = 'ok';
        if (d.state && d.state.e_stop) {
          setStatus('E-STOP active on robot', 'err'); _driving = false;
        }
      } else {
        badge.textContent = 'no robot (sim mode)'; badge.className = 'err';
      }
    } catch(e) {}
  };
  xhr.send();
}, 1000);
</script>
</body>
</html>"""


# ── Keepalive ─────────────────────────────────────────────────────────────────

import threading

def _keepalive_loop():
    while True:
        time.sleep(0.2)
        if _driver is None:
            continue
        if time.monotonic() - _last_drive_t > 0.3:
            # Keep drive servos in WHEEL mode at speed=0 so firmware watchdog
            # stays fed without triggering WHEEL→JOINT mode-switching overhead.
            # Reads current config so servo_ids and steer_center_ticks are correct.
            try:
                cfg = _build_ackermann(_load_config())
                targets = Ackermann(cfg).estop_targets()
                _driver.send_frame(targets, servo_ids=cfg.servo_ids)
            except Exception:
                _driver.send_estop()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _driver, _camera_url

    parser = argparse.ArgumentParser()
    parser.add_argument('--port',       '-p', default=None)
    parser.add_argument('--baud',       '-b', type=int, default=115200)
    parser.add_argument('--ui-port',    '-u', type=int, default=8082)
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
        print('No serial port found - running in simulation mode (no robot).')

    print('Open:  http://localhost:{}'.format(args.ui_port))
    server = ThreadingHTTPServer(('0.0.0.0', args.ui_port), Handler)
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

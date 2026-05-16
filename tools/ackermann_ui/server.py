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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.robot.serial_driver import SerialDriver
from software.robot.ackermann import Ackermann, AckermannConfig

CONFIG_PATH = Path(__file__).parent / 'ackermann_config.json'

_driver = None  # SerialDriver, set in main()


# ── Ackermann helpers ──────────────────────────────────────────────────────────

def _build_cfg(c):
    cfg = AckermannConfig()
    cfg.wheelbase             = float(c.get('wheelbase',             0.20))
    cfg.track_width           = float(c.get('track_width',           0.15))
    cfg.max_steer_deg         = float(c.get('max_steer_deg',         30.0))
    cfg.max_speed_mps         = float(c.get('max_speed_mps',         0.5))
    cfg.max_wheel_speed_ticks = int(c.get('max_wheel_speed_ticks',   300))
    cfg.steer_center_ticks    = int(c.get('steer_center_ticks',      512))
    cfg.steer_dir             = list(c.get('steer_dir',  [1, -1, -1,  1]))
    cfg.drive_dir             = list(c.get('drive_dir',  [1, -1,  1, -1]))
    return cfg


def _compute_result(steer_deg, speed_mps, cfg):
    ack     = Ackermann(cfg)
    targets = ack.compute(steer_deg, speed_mps)
    labels  = ['FL', 'FR', 'RL', 'RR']

    steer_angles = []
    for i in range(4):
        tick  = targets[i].target
        angle = (tick - cfg.steer_center_ticks) / (cfg.steer_dir[i] * cfg.ticks_per_deg)
        steer_angles.append(round(angle, 2))

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

    return {
        'wheels':         wheels,
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
    }


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
            self._send_json({'connected': True,
                             'state': {'seq': s.seq, 'e_stop': s.e_stop, 'servos': servos}})

        elif self.path == '/ports':
            candidates = (glob.glob('/dev/opencm')
                          + glob.glob('/dev/serial/by-id/*ROBOTIS*')
                          + sorted(glob.glob('/dev/ttyACM*')))
            self._send_json({'ports': candidates})

        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        data = self._read_json()

        if self.path == '/compute':
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
            _driver.send_frame(targets)
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
            _driver.send_estop()
            self._send_json({'status': 'e-stop sent'})

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
  #layout { display: grid; grid-template-columns: 420px 1fr; grid-template-rows: auto auto; gap: 12px; padding: 12px; }
  .card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 14px; }
  #viz-card { grid-row: 1 / 3; }
  svg { display: block; margin: 0 auto; }
  .slider-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .slider-row label { width: 90px; color: #aaa; }
  .slider-row input[type=range] { flex: 1; accent-color: #7cf; }
  .slider-row .val { width: 70px; text-align: right; color: #fff; font-weight: bold; }
  .btn { padding: 7px 16px; border: none; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 13px; font-weight: bold; }
  .btn-send  { background: #2a7; color: #fff; }
  .btn-estop { background: #c33; color: #fff; }
  .btn-save  { background: #46a; color: #fff; }
  .btn-load  { background: #555; color: #fff; }
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

<div style="padding:10px 12px 0; display:flex; align-items:center; gap:16px;">
  <h2>Ackermann Config UI</h2>
  <span id="conn-badge" style="font-size:12px;padding:3px 8px;border-radius:10px;background:#333;color:#888;">not connected</span>
</div>

<div id="layout">
  <div class="card" id="viz-card">
    <h3>Bird's-eye view</h3>
    <svg id="robot-svg" width="390" height="440" viewBox="-195 -220 390 440">
      <line x1="-195" y1="0" x2="195" y2="0" stroke="#2a2a2a" stroke-width="1"/>
      <line x1="0" y1="-220" x2="0" y2="220" stroke="#2a2a2a" stroke-width="1"/>
      <rect x="-40" y="-55" width="80" height="110" rx="6" fill="#1e2a3a" stroke="#4af" stroke-width="1.5"/>
      <polygon points="0,-70 -7,-55 7,-55" fill="#4af" opacity="0.7"/>
      <text x="0" y="-80" text-anchor="middle" font-size="10" fill="#4af">FWD</text>
      <g id="wheel-FL"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FL</text></g>
      <g id="wheel-FR"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FR</text></g>
      <g id="wheel-RL"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RL</text></g>
      <g id="wheel-RR"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RR</text></g>
      <g id="arrow-FL"></g>
      <g id="arrow-FR"></g>
      <g id="arrow-RL"></g>
      <g id="arrow-RR"></g>
      <circle id="rc-dot" r="5" fill="#f80" opacity="0"/>
      <text id="rc-label" x="0" y="0" font-size="10" fill="#f80" opacity="0">RC</text>
    </svg>
    <div style="margin-top:8px;">
      <table>
        <tr><th>Wheel</th><th>Steer angle</th><th>Steer tick</th><th>Drive</th><th>Raw</th></tr>
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
    <div class="slider-row">
      <label>Speed (m/s)</label>
      <input type="range" id="sl-speed" min="-0.5" max="0.5" step="0.01" value="0">
      <span class="val"><span id="lbl-speed">0.00</span></span>
    </div>
    <div class="btn-row">
      <button class="btn btn-send"  onclick="sendCmd()">Send to robot</button>
      <button class="btn btn-estop" onclick="sendEstop()">E-STOP</button>
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
  return {
    wheelbase:             parseFloat(document.getElementById('c-wheelbase').value),
    track_width:           parseFloat(document.getElementById('c-track').value),
    max_steer_deg:         parseFloat(document.getElementById('c-maxsteer').value),
    max_speed_mps:         parseFloat(document.getElementById('c-maxspeed').value),
    max_wheel_speed_ticks: parseInt(document.getElementById('c-maxticks').value),
    steer_center_ticks:    parseInt(document.getElementById('c-center').value),
    steer_dir:             parseDir('c-sdir'),
    drive_dir:             parseDir('c-ddir'),
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
}

var slSteer = document.getElementById('sl-steer');
var slSpeed = document.getElementById('sl-speed');

slSteer.addEventListener('input', function() {
  document.getElementById('lbl-steer').textContent = parseFloat(slSteer.value).toFixed(1);
  preview();
});
slSpeed.addEventListener('input', function() {
  document.getElementById('lbl-speed').textContent = parseFloat(slSpeed.value).toFixed(2);
  preview();
});
document.querySelectorAll('.cfg-grid input').forEach(function(el){ el.addEventListener('input', preview); });

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

function sendCmd() {
  var body = { steer_deg: parseFloat(slSteer.value), speed_mps: parseFloat(slSpeed.value), config: readConfig() };
  postJSON('/send', body, function(err, d) {
    if (err || d.error) { setStatus('Robot: ' + (d && d.error ? d.error : err), true); return; }
    setStatus('Sent  steer=' + body.steer_deg.toFixed(1) + ' deg  speed=' + body.speed_mps.toFixed(2) + ' m/s');
    updateViz(d);
  });
}

function sendEstop() {
  postJSON('/estop', {}, function(err) {
    setStatus(err ? 'Estop error: ' + err : 'E-STOP sent.', !!err);
  });
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
  var tbody = document.getElementById('wheel-table');
  tbody.innerHTML = '';
  wheels.forEach(function(w) {
    var color = w.drive_dir === 'CCW' ? '#4f4' : w.drive_dir === 'CW' ? '#f84' : '#888';
    var sign  = w.steer_angle >= 0 ? '+' : '';
    tbody.innerHTML +=
      '<tr><td class="hl">' + w.label + '</td>' +
      '<td>' + sign + w.steer_angle.toFixed(1) + ' deg</td>' +
      '<td>' + w.steer_tick + '</td>' +
      '<td style="color:' + color + '">' + w.drive_dir + ' ' + w.drive_mag + '</td>' +
      '<td style="color:#555">' + w.drive_raw + '</td></tr>';
  });

  document.getElementById('tr-val').textContent =
    data.turning_radius !== null ? data.turning_radius + ' m' : 'inf (straight)';
  var cs = data.steer_clamped;
  document.getElementById('cs-val').textContent = (cs >= 0 ? '+' : '') + cs.toFixed(1);

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
    var rcX  = sign * Math.min(data.turning_radius * 150, 180);
    rc.setAttribute('cx', rcX); rc.setAttribute('cy', 0); rc.setAttribute('opacity', 0.8);
    rcLbl.setAttribute('x', rcX + 7); rcLbl.setAttribute('y', 4); rcLbl.setAttribute('opacity', 0.8);
  } else {
    rc.setAttribute('opacity', 0); rcLbl.setAttribute('opacity', 0);
  }
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
    var tbody = document.getElementById('state-table');
    if (!d.state) { tbody.innerHTML = '<tr><td colspan="6" style="color:#555">waiting...</td></tr>'; return; }
    tbody.innerHTML = d.state.servos.map(function(s) {
      if (!s.available) return '<tr><td>' + s.id + '</td><td colspan="5" style="color:#555">UNAVAIL</td></tr>';
      return '<tr><td>' + s.id + '</td><td>' + s.mode + '</td><td>' + s.pos + '</td>' +
             '<td>' + s.speed + '</td><td>' + s.temp_c + 'C</td><td>' + s.volt_v.toFixed(1) + 'V</td></tr>';
    }).join('');
    if (d.state.e_stop) setStatus('E-STOP active on robot', true);
  });
}

function setStatus(msg, err) {
  var el = document.getElementById('status-bar');
  el.textContent = msg;
  el.className = err ? 'err' : '';
}

loadConfig();
setInterval(preview, 300);
setInterval(refreshState, 1000);
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _driver

    parser = argparse.ArgumentParser()
    parser.add_argument('--port',    '-p', default=None)
    parser.add_argument('--baud',    '-b', type=int, default=115200)
    parser.add_argument('--ui-port', '-u', type=int, default=8080)
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

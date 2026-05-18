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


# ── HTML dashboard ─────────────────────────────────────────────────────────────

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
  font-size: 13px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
h3 { color: #adf; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }

/* ── top bar ── */
#topbar {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 14px;
  background: #111; border-bottom: 1px solid #222;
  flex-shrink: 0;
}
h1 { color: #7cf; font-size: 14px; letter-spacing: 2px; }
#conn-badge {
  font-size: 22px; padding: 2px 8px; border-radius: 10px;
  background: #222; color: #666;
}
#conn-badge.ok  { background: #0d2a0d; color: #4f4; }
#conn-badge.err { background: #2a0d0d; color: #f44; }
#topstatus { font-size: 22px; color: #555; }
#topstatus.driving { color: #5fa; }
#topstatus.err     { color: #f66; }
.btn-estop-top {
  padding: 5px 16px; border: none; border-radius: 4px;
  background: #7a1515; color: #fff;
  font-family: monospace; font-size: 12px; font-weight: bold;
  cursor: pointer;
}
.btn-estop-top:hover { background: #c22; }

/* ── main layout ── */
#layout {
  display: grid;
  grid-template-columns: 430px 1fr 248px;
  gap: 8px; padding: 8px;
  flex: 1; min-height: 0;
}
.card {
  background: #141414;
  border: 1px solid #222;
  border-radius: 5px;
  padding: 10px;
  overflow: hidden;
}

/* ── left column ── */
#left-col { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }
svg { display: block; margin: 0 auto; }

/* ── centre column ── */
#center-col { display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
#cam-wrap {
  flex: 1; display: flex; align-items: center; justify-content: center;
  background: #050505; border-radius: 4px; overflow: hidden;
  min-height: 0;
}
#cam-img { transform: rotate(180deg); }
#cam-placeholder { color: #333; font-size: 12px; text-align: center; }
#wasd-card { flex-shrink: 0; }

/* ── controls (inside left card) ── */
.wasd-grid { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.wasd-row  { display: flex; gap: 4px; }
.kb-key {
  width: 62px; height: 62px;
  border: 2px solid #2a2a2a; border-radius: 9px;
  background: #1a1a1a; color: #555;
  display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: bold; user-select: none;
  transition: background 0.05s, color 0.05s, border-color 0.05s;
}
.kb-key .sub { font-size: 8px; color: #444; margin-top: 2px; text-align: center; }
.kb-key-inner { display: flex; flex-direction: column; align-items: center; }
.kb-key.active { background: #1a5a2a; color: #5fe; border-color: #4fa; }
.gauge-wrap { display: flex; flex-direction: column; gap: 2px; }
.gauge-label { font-size: 10px; color: #555; text-transform: uppercase; }
.gauge-bar-track {
  width: 160px; height: 16px;
  background: #161616; border: 1px solid #2a2a2a; border-radius: 3px;
  position: relative; overflow: hidden;
}
.gauge-bar-fill {
  position: absolute; top: 0; height: 100%; border-radius: 2px;
  transition: left 0.08s, width 0.08s;
}
.gauge-center-line { position: absolute; left: 50%; top: 0; width: 1px; height: 100%; background: #2a2a2a; }
.gauge-val { font-size: 12px; font-weight: bold; color: #7cf; }

/* ── right column ── */
#right-col { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th { color: #7cf; font-weight: normal; text-align: left; padding: 2px 3px; border-bottom: 1px solid #222; }
td { padding: 2px 3px; }
tr:nth-child(even) td { background: #181818; }
</style>
</head>
<body>

<!-- top bar -->
<div id="topbar">
  <h1>DRIVE DASHBOARD</h1>
  <span id="conn-badge">connecting...</span>
  <span id="topstatus">Click page, then use WASD to drive.</span>
</div>

<div id="layout">

  <!-- ── Left: bird's-eye + WASD controls ─────────────────────────────────── -->
  <div id="left-col">

    <!-- Bird's-eye -->
    <div class="card" style="min-height:0;overflow:hidden;flex-shrink:0;">
      <h3>Bird's-eye</h3>
      <svg id="robot-svg" width="406" height="332" viewBox="-183 -150 366 300" overflow="hidden">
        <line x1="-183" y1="0" x2="183" y2="0" stroke="#1a1a1a" stroke-width="1"/>
        <line x1="0" y1="-150" x2="0" y2="150" stroke="#1a1a1a" stroke-width="1"/>
        <g id="ackermann-arcs"></g>
        <!-- robot body -->
        <rect x="-32" y="-46" width="64" height="92" rx="5" fill="#151f2e" stroke="#4af" stroke-width="1.5"/>
        <polygon points="0,-58 -5,-46 5,-46" fill="#4af" opacity="0.7"/>
        <text x="0" y="-74" text-anchor="middle" font-size="9" fill="#4af">FWD</text>
        <!-- wheels: initial transforms set so layout is correct before first poll -->
        <!-- wheels: angle label lives inside the group so it stays with the wheel label -->
        <g id="wheel-FL" transform="translate(-55,-46)">
          <rect id="wheel-rect-FL" x="-8" y="-14" width="16" height="28" rx="3" fill="#336"/>
          <circle id="wheel-joint-FL" cx="0" cy="0" r="3.5" fill="#4af" stroke="#111" stroke-width="1"/>
          <line x1="0" y1="-17" x2="0" y2="17" stroke="#6af" stroke-width="1" opacity="0.5"/>
          <text x="0" y="24" text-anchor="middle" font-size="9" fill="#99f">FL</text>
          <text id="angle-FL" x="0" y="34" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
          <!-- temp labels: local coords, x negative = left of wheel, right-edge anchored -->
          <text id="temp-s-FL" x="-13" y="-6" text-anchor="end" font-size="8" fill="#4af">S:—</text>
          <text id="temp-d-FL" x="-13" y="10" text-anchor="end" font-size="8" fill="#336">D:—</text>
        </g>
        <g id="wheel-FR" transform="translate(55,-46)">
          <rect id="wheel-rect-FR" x="-8" y="-14" width="16" height="28" rx="3" fill="#336"/>
          <circle id="wheel-joint-FR" cx="0" cy="0" r="3.5" fill="#4af" stroke="#111" stroke-width="1"/>
          <line x1="0" y1="-17" x2="0" y2="17" stroke="#6af" stroke-width="1" opacity="0.5"/>
          <text x="0" y="24" text-anchor="middle" font-size="9" fill="#99f">FR</text>
          <text id="angle-FR" x="0" y="34" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
          <!-- temp labels: local coords, x positive = right of wheel, left-edge anchored -->
          <text id="temp-s-FR" x="13" y="-6" text-anchor="start" font-size="8" fill="#4af">S:—</text>
          <text id="temp-d-FR" x="13" y="10" text-anchor="start" font-size="8" fill="#336">D:—</text>
        </g>
        <g id="wheel-RL" transform="translate(-55,46)">
          <rect id="wheel-rect-RL" x="-8" y="-14" width="16" height="28" rx="3" fill="#336"/>
          <circle id="wheel-joint-RL" cx="0" cy="0" r="3.5" fill="#4af" stroke="#111" stroke-width="1"/>
          <line x1="0" y1="-17" x2="0" y2="17" stroke="#6af" stroke-width="1" opacity="0.5"/>
          <text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RL</text>
          <text id="angle-RL" x="0" y="-30" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
          <text id="temp-s-RL" x="-13" y="-6" text-anchor="end" font-size="8" fill="#4af">S:—</text>
          <text id="temp-d-RL" x="-13" y="10" text-anchor="end" font-size="8" fill="#336">D:—</text>
        </g>
        <g id="wheel-RR" transform="translate(55,46)">
          <rect id="wheel-rect-RR" x="-8" y="-14" width="16" height="28" rx="3" fill="#336"/>
          <circle id="wheel-joint-RR" cx="0" cy="0" r="3.5" fill="#4af" stroke="#111" stroke-width="1"/>
          <line x1="0" y1="-17" x2="0" y2="17" stroke="#6af" stroke-width="1" opacity="0.5"/>
          <text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RR</text>
          <text id="angle-RR" x="0" y="-30" text-anchor="middle" font-size="9" fill="#fa0">0.0°</text>
          <text id="temp-s-RR" x="13" y="-6" text-anchor="start" font-size="8" fill="#4af">S:—</text>
          <text id="temp-d-RR" x="13" y="10" text-anchor="start" font-size="8" fill="#336">D:—</text>
        </g>
        <!-- drive speed arrows -->
        <g id="arrow-FL"></g><g id="arrow-FR"></g>
        <g id="arrow-RL"></g><g id="arrow-RR"></g>
        <!-- RC dot -->
        <circle id="rc-dot" r="4" fill="#f80" opacity="0"/>
        <text id="rc-label" x="0" y="0" font-size="9" fill="#f80" opacity="0">RC</text>
        <!-- legend -->
        <g transform="translate(-181,91)" font-size="9" fill="#777">
          <rect x="-2" y="-2" width="140" height="72" rx="3" fill="#111" stroke="#2a2a2a" stroke-width="0.8" opacity="0.95"/>
          <text y="10"><tspan font-weight="bold" fill="#bbb">FL/FR</tspan>  Front L / R</text>
          <text y="22"><tspan font-weight="bold" fill="#bbb">RL/RR</tspan>  Rear L / R</text>
          <text y="34"><tspan font-weight="bold" fill="#f80">RC</tspan>  Rotation centre</text>
          <text y="46"><tspan font-weight="bold" fill="#4af">S</tspan>  Steer temp (joint)</text>
          <text y="58"><tspan font-weight="bold" fill="#1a6a3a">D</tspan>  Drive temp (rect)</text>
        </g>
      </svg>
    </div>

  </div>

  <!-- ── Centre: camera + WASD ────────────────────────────────────────────── -->
  <div id="center-col">
    <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden;">
      <h3>Camera</h3>
      <div id="cam-wrap" style="flex:1;min-height:0;overflow:hidden;">
        <img id="cam-img" src="/camera" alt=""
             style="width:100%;height:100%;object-fit:contain;display:block;"
             onerror="this.style.display='none';document.getElementById('cam-placeholder').style.display='block'">
        <div id="cam-placeholder" style="display:none;">
          No camera stream<br>
          <span style="color:#222;font-size:10px;">__CAMERA_URL__</span>
        </div>
      </div>
    </div>
    <!-- WASD + gauges -->
    <div class="card" id="wasd-card">
      <div id="cfg-info" style="font-size:11px;color:#555;text-align:center;margin-bottom:6px;">loading...</div>
      <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap;justify-content:center;">

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
        <div style="display:flex;flex-direction:column;gap:6px;">
          <div class="gauge-wrap">
            <div class="gauge-label">Steer</div>
            <div class="gauge-bar-track">
              <div id="gauge-steer" class="gauge-bar-fill" style="left:50%;width:0%;background:#7cf;"></div>
              <div class="gauge-center-line"></div>
            </div>
            <div id="val-steer" class="gauge-val">0.0 °</div>
          </div>
          <div class="gauge-wrap">
            <div class="gauge-label">Output</div>
            <div class="gauge-bar-track">
              <div id="gauge-speed" class="gauge-bar-fill" style="left:50%;width:0%;background:#4fa;"></div>
              <div class="gauge-center-line"></div>
            </div>
            <div id="val-speed" class="gauge-val">0 %</div>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;font-size:20px;color:#444;">
          <div><span style="color:#7cf;font-weight:bold;">W/S</span>  fwd / rev</div>
          <div><span style="color:#7cf;font-weight:bold;">A/D</span>  steer</div>
          <div><span style="color:#7cf;font-weight:bold;">Spc/Esc</span>  e-stop</div>
        </div>
      </div>
      <div style="display:flex;justify-content:center;margin-top:10px;">
        <button class="btn-estop-top" onclick="doEstop()">&#9632; E-STOP</button>
      </div>
    </div>
  </div>

  <!-- ── Right: battery + servo state ─────────────────────────────────────── -->
  <div id="right-col">

    <!-- Battery card -->
    <div class="card">
      <h3>Battery</h3>
      <div style="text-align:center;margin:6px 0 4px;">
        <svg width="158" height="60" viewBox="0 0 158 60" style="display:inline-block;">
          <!-- body -->
          <rect x="2" y="5" width="138" height="50" rx="5" fill="none" stroke="#555" stroke-width="2"/>
          <!-- terminal nub -->
          <rect x="140" y="20" width="14" height="20" rx="3" fill="#555"/>
          <!-- fill bar (max usable width = 132px, from x=5 to x=137) -->
          <rect id="batt-fill" x="5" y="8" width="0" height="44" rx="3" fill="#4f4"/>
          <!-- percentage text -->
          <text id="batt-pct-icon" x="70" y="37" text-anchor="middle" dominant-baseline="middle"
                font-family="monospace" font-size="20" font-weight="bold" fill="#fff">—</text>
        </svg>
      </div>
    </div>

  </div>
</div>

<script>
var _keys         = { w: false, a: false, s: false, d: false };
var _maxSteer     = 30.0;
var _driving      = false;
var _cfg          = null;
var _battSamples    = [];   // voltage readings accumulated between display updates
var _lastBattDispMs = 0;    // timestamp of last battery display update
var WHEEL_POS  = { FL:{x:-55,y:-46}, FR:{x:55,y:-46}, RL:{x:-55,y:46}, RR:{x:55,y:46} };
var WHEEL_ORDER = ['FL','FR','RL','RR'];

// ── Config ────────────────────────────────────────────────────────────────────
function loadConfig() {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/config');
  xhr.onload = function() {
    try {
      _cfg = JSON.parse(xhr.responseText);
      _maxSteer = parseFloat(_cfg.max_steer_deg) || 30;
      var pct = (Math.min(parseInt(_cfg.max_wheel_speed_ticks||300),1023)/1023*100).toFixed(0);
      document.getElementById('cfg-info').textContent =
        'max steer: ' + _maxSteer + '°  |  max output: ' + pct + '%';
    } catch(e) {}
  };
  xhr.send();
}
loadConfig();
setInterval(loadConfig, 5000);

// ── Temperature colour ────────────────────────────────────────────────────────
function tempColor(t) {
  if (t < 35) return '#1a4a8a';   // cool blue
  if (t < 45) return '#1a6a3a';   // green
  if (t < 55) return '#7a6a10';   // yellow
  if (t < 65) return '#8a4010';   // orange
  return '#8a1515';               // hot red
}

// ── State poll ────────────────────────────────────────────────────────────────
function pollState() {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/state');
  xhr.timeout = 800;
  xhr.onload = function() {
    try { updatePanels(JSON.parse(xhr.responseText)); } catch(e) {}
  };
  xhr.send();
}

function updatePanels(d) {
  var badge = document.getElementById('conn-badge');
  if (!d.connected) {
    badge.textContent = 'no robot (sim)'; badge.className = 'err';
    return;
  }
  badge.textContent = 'connected'; badge.className = 'ok';
  if (!d.state) return;
  if (d.state.e_stop) { _driving = false; }

  var cfg  = _cfg || {};
  var sids = cfg.servo_ids || [4,2,8,6,3,1,7,5];

  // ── Battery — 3 s averaging window, display refresh every 3 s ────────────
  var maxV  = parseFloat(cfg.batt_max_v)      || 12.6;
  var okV   = parseFloat(cfg.batt_ok_v)       || 11.0;
  var lowV  = parseFloat(cfg.batt_low_v)      || 10.2;
  var critV = parseFloat(cfg.batt_critical_v) || 9.6;
  var avail = d.state.servos.filter(function(s){ return s.available; });
  if (avail.length > 0) {
    var instV = avail.reduce(function(sum,s){ return sum + s.volt_v; }, 0) / avail.length;
    _battSamples.push(instV);
  }
  var nowMs = Date.now();
  if (nowMs - _lastBattDispMs >= 3000 && _battSamples.length > 0) {
    var avgV   = _battSamples.reduce(function(a,b){ return a+b; }, 0) / _battSamples.length;
    _battSamples   = [];
    _lastBattDispMs = nowMs;
    var bColor;
    if (avgV >= okV)        { bColor = '#4f4'; }
    else if (avgV >= lowV)  { bColor = '#af4'; }
    else if (avgV >= critV) { bColor = '#fa4'; }
    else                    { bColor = '#f44'; }
    var pct    = Math.max(0, Math.min(100, (avgV - critV) / (maxV - critV) * 100));
    var fillPx = Math.round(pct / 100 * 132);
    document.getElementById('batt-fill').setAttribute('width', fillPx);
    document.getElementById('batt-fill').setAttribute('fill', bColor);
    document.getElementById('batt-pct-icon').textContent = pct.toFixed(0) + '%';
    document.getElementById('batt-pct-icon').setAttribute('fill', pct < 20 ? bColor : '#fff');
  }

  // ── Wheel temperatures + heatmap colours ─────────────────────────────────
  WHEEL_ORDER.forEach(function(lbl, i) {
    var steerSv = d.state.servos[sids[i]     - 1];
    var driveSv = d.state.servos[sids[i + 4] - 1];
    if (steerSv && steerSv.available) {
      var sc = tempColor(steerSv.temp_c);
      document.getElementById('wheel-joint-' + lbl).setAttribute('fill', sc);
      var ts = document.getElementById('temp-s-' + lbl);
      if (ts) { ts.textContent = 'S:' + steerSv.temp_c + '°C'; ts.setAttribute('fill', sc); }
    }
    if (driveSv && driveSv.available) {
      var dc = tempColor(driveSv.temp_c);
      document.getElementById('wheel-rect-' + lbl).setAttribute('fill', dc);
      var td = document.getElementById('temp-d-' + lbl);
      if (td) { td.textContent = 'D:' + driveSv.temp_c + '°C'; td.setAttribute('fill', dc); }
    }
  });

  // ── Bird's-eye: actual steer angles + drive arrows ────────────────────────
  var tpd     = 1023 / 300;
  var center  = parseInt(cfg.steer_center_ticks) || 512;
  var sDir    = cfg.steer_dir      || [1,-1,-1,1];
  var dDir    = cfg.drive_dir      || [1,-1,1,-1];
  var offsets = cfg.steer_offset_deg || [0,0,0,0];
  var maxSt   = parseFloat(cfg.max_steer_deg) || 30;

  var steerAngles = WHEEL_ORDER.map(function(lbl, i) {
    var sv = d.state.servos[sids[i] - 1];
    if (!sv || !sv.available || sDir[i] === 0) return 0;
    return Math.max(-maxSt, Math.min(maxSt,
      (sv.pos - center) / (sDir[i] * tpd) - offsets[i]));
  });

  WHEEL_ORDER.forEach(function(lbl, i) {
    var pos = WHEEL_POS[lbl];
    var ang = steerAngles[i];
    document.getElementById('wheel-' + lbl).setAttribute(
      'transform', 'translate(' + pos.x + ',' + pos.y + ') rotate(' + ang + ')');
    var labelEl = document.getElementById('angle-' + lbl);
    if (labelEl) labelEl.textContent = (ang >= 0 ? '+' : '') + ang.toFixed(1) + '°';
  });

  updateAckermannArcs(steerAngles[0], cfg);
  updateRcDot(steerAngles[0], cfg);

  WHEEL_ORDER.forEach(function(lbl, i) {
    var dSv = d.state.servos[sids[i + 4] - 1];
    var arr = document.getElementById('arrow-' + lbl);
    arr.innerHTML = '';
    if (!dSv || !dSv.available) return;
    var raw = dSv.speed;
    if (raw === 0 || raw === 1024) return;
    var physMag, physFwd;
    if (raw < 1024) { physMag = raw;        physFwd = dDir[i] > 0; }
    else            { physMag = raw - 1024;  physFwd = dDir[i] < 0; }
    var len   = Math.min(physMag / 1023, 1) * 24 + 5;
    var dir   = physFwd ? -1 : 1;
    var y2    = dir * len;
    var tipY  = y2 + dir * 4;
    var color = physFwd ? '#4f4' : '#f84';
    var ang   = steerAngles[i];
    var pos   = WHEEL_POS[lbl];
    arr.innerHTML =
      '<g transform="translate(' + pos.x + ',' + pos.y + ') rotate(' + ang + ')">' +
      '<line x1="0" y1="0" x2="0" y2="' + y2 + '" stroke="' + color + '" stroke-width="2"/>' +
      '<polygon points="0,' + tipY + ' -3,' + y2 + ' 3,' + y2 + '" fill="' + color + '"/>' +
      '</g>';
  });

}
setInterval(pollState, 100);

// ── Ackermann arcs ────────────────────────────────────────────────────────────
function updateAckermannArcs(steer, cfg) {
  var el = document.getElementById('ackermann-arcs');
  if (!el) return;
  var L2 = (parseFloat(cfg.wheelbase)   || 0.20) / 2;
  var W2 = (parseFloat(cfg.track_width) || 0.15) / 2;
  if (Math.abs(steer) < 0.5) { el.innerHTML = ''; return; }
  var sign  = steer > 0 ? 1 : -1;
  var dRad  = Math.abs(steer) * Math.PI / 180;
  var R     = L2 / Math.tan(dRad);
  var scaleX = 55 / W2;
  var irc_x  = sign * R * scaleX;
  var dxOut  = irc_x + sign * 55;
  var dxIn   = irc_x - sign * 55;
  var rOuter = Math.sqrt(dxOut * dxOut + 46 * 46);
  var rInner = Math.sqrt(dxIn  * dxIn  + 46 * 46);
  var rCtr   = Math.abs(irc_x);
  el.innerHTML =
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rOuter.toFixed(1) +
      '" fill="none" stroke="#2a5a8a" stroke-width="1" stroke-dasharray="5,4" opacity="0.45"/>' +
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rCtr.toFixed(1) +
      '" fill="none" stroke="#3a7aaa" stroke-width="1" stroke-dasharray="3,4" opacity="0.45"/>' +
    '<circle cx="' + irc_x.toFixed(1) + '" cy="0" r="' + rInner.toFixed(1) +
      '" fill="none" stroke="#2a5a8a" stroke-width="1" stroke-dasharray="5,4" opacity="0.45"/>';
}

function updateRcDot(steer, cfg) {
  var rc    = document.getElementById('rc-dot');
  var rcLbl = document.getElementById('rc-label');
  if (Math.abs(steer) < 0.5) { rc.setAttribute('opacity',0); rcLbl.setAttribute('opacity',0); return; }
  var L2  = (parseFloat(cfg.wheelbase)   || 0.20) / 2;
  var W2  = (parseFloat(cfg.track_width) || 0.15) / 2;
  var R   = L2 / Math.tan(Math.abs(steer) * Math.PI / 180);
  var scaleX = 55 / W2;
  var rcX = (steer > 0 ? 1 : -1) * R * scaleX;
  rc.setAttribute('cx', rcX); rc.setAttribute('cy', 0); rc.setAttribute('opacity', 0.8);
  rcLbl.setAttribute('x', rcX + 6); rcLbl.setAttribute('y', 4); rcLbl.setAttribute('opacity', 0.8);
}

// ── WASD drive ────────────────────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  var k = e.key.toLowerCase();
  if (k === 'w' || k === 'a' || k === 's' || k === 'd') {
    e.preventDefault(); _keys[k] = true; updateKeyDisplay();
  } else if (k === ' ' || k === 'escape') {
    e.preventDefault(); doEstop();
  }
});
document.addEventListener('keyup', function(e) {
  var k = e.key.toLowerCase();
  if (_keys.hasOwnProperty(k)) { _keys[k] = false; updateKeyDisplay(); }
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
  if (_keys.w) speed += 1.0;
  if (_keys.s) speed -= 1.0;
  updateGauges(steer, speed);
  if (anyKey) {
    _driving = true;
    sendDrive(steer, speed);
  } else if (_driving) {
    _driving = false;
    sendDrive(0, 0);
  }
}, 100);

function sendDrive(steer, speed) {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/drive');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() {};
  xhr.onerror = function() {};
  xhr.send(JSON.stringify({ steer_deg: steer, speed_mps: speed }));
}

function doEstop() {
  _driving = false;
  _keys = { w:false, a:false, s:false, d:false };
  updateKeyDisplay(); updateGauges(0, 0);
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/estop');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() {};
  xhr.send('{}');
}

function updateGauges(steer, speed) {
  var sp = (steer / _maxSteer) * 50;
  var gs = document.getElementById('gauge-steer');
  if (sp >= 0) { gs.style.left='50%'; gs.style.width=sp+'%'; gs.style.background='#7cf'; }
  else         { gs.style.left=(50+sp)+'%'; gs.style.width=(-sp)+'%'; gs.style.background='#fa8'; }
  document.getElementById('val-steer').textContent = (steer>=0?'+':'')+steer.toFixed(1)+' °';

  var vp = speed * 50;
  var gv = document.getElementById('gauge-speed');
  if (vp >= 0) { gv.style.left='50%'; gv.style.width=vp+'%'; gv.style.background='#4fa'; }
  else         { gv.style.left=(50+vp)+'%'; gv.style.width=(-vp)+'%'; gv.style.background='#f84'; }
  document.getElementById('val-speed').textContent = (speed>=0?'+':'')+Math.round(speed*100)+'%';
}

</script>
</body>
</html>"""


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
    global _driver, _camera_url

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

    print('Camera : {}'.format(_camera_url))
    print('Open   : http://localhost:{}'.format(args.ui_port))
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

#!/usr/bin/env python3
"""
ackermann_ui/server.py — Browser-based Ackermann config and test UI.

Serves a single HTML page that lets you:
  • Visualise wheel steering angles and drive speeds (bird's-eye SVG)
  • Tune every AckermannConfig parameter live
  • Send commands to the real robot (optional — works without hardware)
  • Save / load config as JSON

Run:
    python3 tools/ackermann_ui/server.py
    python3 tools/ackermann_ui/server.py --port /dev/ttyACM1

Then open:  http://<jetson-ip>:8080
"""

import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from software.robot.serial_driver import SerialDriver, ServoCmd
from software.robot.ackermann import Ackermann, AckermannConfig

# ── Config file path ───────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / 'ackermann_config.json'

# ── Global robot driver (optional) ────────────────────────────────────────────
_driver: Optional[SerialDriver] = None


def get_driver() -> Optional[SerialDriver]:
    return _driver


# ── Pydantic models ────────────────────────────────────────────────────────────

class ConfigModel(BaseModel):
    wheelbase:            float = 0.20
    track_width:          float = 0.15
    max_steer_deg:        float = 30.0
    max_speed_mps:        float = 0.5
    max_wheel_speed_ticks: int  = 300
    steer_center_ticks:   int  = 512
    steer_dir:            list  = [1, -1, -1, 1]
    drive_dir:            list  = [1, -1,  1, -1]


class ComputeRequest(BaseModel):
    steer_deg: float = 0.0
    speed_mps: float = 0.0
    config:    ConfigModel = ConfigModel()


# ── Ackermann compute helper ───────────────────────────────────────────────────

def _build_cfg(c: ConfigModel) -> AckermannConfig:
    cfg = AckermannConfig()
    cfg.wheelbase             = c.wheelbase
    cfg.track_width           = c.track_width
    cfg.max_steer_deg         = c.max_steer_deg
    cfg.max_speed_mps         = c.max_speed_mps
    cfg.max_wheel_speed_ticks = c.max_wheel_speed_ticks
    cfg.steer_center_ticks    = c.steer_center_ticks
    cfg.steer_dir             = list(c.steer_dir)
    cfg.drive_dir             = list(c.drive_dir)
    return cfg


def _compute_result(steer_deg: float, speed_mps: float, cfg: AckermannConfig) -> dict:
    ack     = Ackermann(cfg)
    targets = ack.compute(steer_deg, speed_mps)

    labels = ['FL', 'FR', 'RL', 'RR']

    # Decode physical steering angles back from ticks
    steer_angles = []
    for i in range(4):
        tick  = targets[i].target
        angle = (tick - cfg.steer_center_ticks) / (cfg.steer_dir[i] * cfg.ticks_per_deg)
        steer_angles.append(round(angle, 2))

    # Decode drive speed magnitudes and direction
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

    # Turning radius
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


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI()


@app.get('/', response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post('/compute')
async def compute(req: ComputeRequest):
    cfg    = _build_cfg(req.config)
    result = _compute_result(req.steer_deg, req.speed_mps, cfg)
    return result


@app.post('/send')
async def send(req: ComputeRequest):
    driver = get_driver()
    if driver is None:
        return JSONResponse({'error': 'No robot connected (server started without --port)'}, status_code=503)
    cfg     = _build_cfg(req.config)
    ack     = Ackermann(cfg)
    targets = ack.compute(req.steer_deg, req.speed_mps)
    driver.send_frame(targets)
    result  = _compute_result(req.steer_deg, req.speed_mps, cfg)
    result['sent'] = True
    return result


@app.post('/estop')
async def estop():
    driver = get_driver()
    if driver is None:
        return JSONResponse({'error': 'No robot connected'}, status_code=503)
    driver.send_estop()
    return {'status': 'e-stop sent'}


@app.get('/state')
async def state():
    driver = get_driver()
    if driver is None:
        return JSONResponse({'connected': False})
    s = driver.get_state()
    if s is None:
        return {'connected': True, 'state': None}
    servos = [
        {
            'id': sv.servo_id, 'available': sv.available,
            'mode': 'WHEEL' if sv.mode else 'JOINT',
            'pos': sv.pos, 'speed': sv.speed,
            'temp_c': sv.temperature, 'volt_v': sv.voltage,
        }
        for sv in s.servos
    ]
    return {'connected': True, 'state': {'seq': s.seq, 'e_stop': s.e_stop, 'servos': servos}}


@app.get('/config')
async def config_load():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return ConfigModel().dict()


@app.post('/config')
async def config_save(cfg: ConfigModel):
    CONFIG_PATH.write_text(json.dumps(cfg.dict(), indent=2))
    return {'saved': str(CONFIG_PATH)}


@app.get('/ports')
async def list_ports():
    candidates = (
        glob.glob('/dev/opencm')
        + glob.glob('/dev/serial/by-id/*ROBOTIS*')
        + sorted(glob.glob('/dev/ttyACM*'))
    )
    return {'ports': candidates}


# ── HTML page ──────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ackermann Config UI — Black-Mata</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: monospace; background: #111; color: #ddd; font-size: 14px; }
  h2 { color: #7cf; margin-bottom: 8px; }
  h3 { color: #adf; margin-bottom: 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }

  #layout { display: grid; grid-template-columns: 420px 1fr; grid-template-rows: auto auto; gap: 12px; padding: 12px; }

  .card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 14px; }

  /* SVG panel */
  #viz-card { grid-row: 1 / 3; }
  svg { display: block; margin: 0 auto; }

  /* Controls */
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
  #btn-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }

  /* Results table */
  table { width: 100%; border-collapse: collapse; }
  th { color: #7cf; font-weight: normal; text-align: left; padding: 3px 6px; border-bottom: 1px solid #333; }
  td { padding: 3px 6px; }
  tr:nth-child(even) td { background: #222; }
  .highlight { color: #7fc; font-weight: bold; }

  /* Config grid */
  .cfg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
  .cfg-row { display: flex; align-items: center; gap: 6px; }
  .cfg-row label { width: 160px; color: #aaa; font-size: 12px; }
  .cfg-row input { width: 80px; background: #222; border: 1px solid #444; color: #fff; padding: 3px 6px; border-radius: 3px; font-family: monospace; }
  .cfg-row input:focus { outline: none; border-color: #7cf; }

  #status-bar { padding: 6px 12px; background: #0a2a0a; border-top: 1px solid #333; font-size: 12px; color: #8a8; }
  #status-bar.err { background: #2a0a0a; color: #f88; }

  #robot-label { font-size: 11px; fill: #888; }
</style>
</head>
<body>

<div style="padding: 10px 12px 0; display:flex; align-items:center; gap:16px;">
  <h2>⚙ Ackermann Config UI</h2>
  <span id="conn-badge" style="font-size:12px; padding:3px 8px; border-radius:10px; background:#333; color:#888;">● not connected</span>
</div>

<div id="layout">

  <!-- Bird's-eye visualisation -->
  <div class="card" id="viz-card">
    <h3>Bird's-eye view</h3>
    <svg id="robot-svg" width="390" height="440" viewBox="-195 -220 390 440">
      <!-- Grid lines -->
      <line x1="-195" y1="0" x2="195" y2="0" stroke="#2a2a2a" stroke-width="1"/>
      <line x1="0" y1="-220" x2="0" y2="220" stroke="#2a2a2a" stroke-width="1"/>
      <!-- Robot body -->
      <rect id="body" x="-40" y="-55" width="80" height="110" rx="6"
            fill="#1e2a3a" stroke="#4af" stroke-width="1.5"/>
      <!-- Forward arrow -->
      <polygon points="0,-70 -7,-55 7,-55" fill="#4af" opacity="0.7"/>
      <text x="0" y="-80" text-anchor="middle" font-size="10" fill="#4af">FWD</text>
      <!-- Wheels: FL FR RL RR -->
      <g id="wheel-FL"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FL</text></g>
      <g id="wheel-FR"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="28" text-anchor="middle" font-size="9" fill="#99f">FR</text></g>
      <g id="wheel-RL"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RL</text></g>
      <g id="wheel-RR"><rect x="-9" y="-16" width="18" height="32" rx="3" fill="#336"/><text x="0" y="-20" text-anchor="middle" font-size="9" fill="#99f">RR</text></g>
      <!-- Speed arrows (one per wheel) -->
      <g id="arrow-FL"></g>
      <g id="arrow-FR"></g>
      <g id="arrow-RL"></g>
      <g id="arrow-RR"></g>
      <!-- Rotation centre marker -->
      <circle id="rc-dot" r="5" fill="#f80" opacity="0" />
      <text id="rc-label" x="0" y="0" font-size="10" fill="#f80" opacity="0">RC</text>
    </svg>

    <!-- Live angle/speed readout under SVG -->
    <div style="margin-top:8px;">
      <table>
        <tr><th>Wheel</th><th>Steer angle</th><th>Steer tick</th><th>Drive</th><th>Raw</th></tr>
        <tbody id="wheel-table"></tbody>
      </table>
      <div style="margin-top:8px; color:#888; font-size:12px;">
        Turning radius: <span id="tr-val" style="color:#f80">—</span> &nbsp;|&nbsp;
        Clamped steer: <span id="cs-val" style="color:#adf">—</span>°
      </div>
    </div>
  </div>

  <!-- Controls -->
  <div class="card">
    <h3>Drive command</h3>
    <div class="slider-row">
      <label>Steer (°)</label>
      <input type="range" id="sl-steer" min="-30" max="30" step="0.5" value="0">
      <span class="val"><span id="lbl-steer">0.0</span>°</span>
    </div>
    <div class="slider-row">
      <label>Speed (m/s)</label>
      <input type="range" id="sl-speed" min="-0.5" max="0.5" step="0.01" value="0">
      <span class="val"><span id="lbl-speed">0.00</span></span>
    </div>
    <div id="btn-row">
      <button class="btn btn-send"  onclick="sendCmd()">▶ Send to robot</button>
      <button class="btn btn-estop" onclick="sendEstop()">■ E-STOP</button>
    </div>
  </div>

  <!-- Config + save/load -->
  <div class="card">
    <h3>AckermannConfig</h3>
    <div class="cfg-grid">
      <div class="cfg-row"><label>wheelbase (m)</label>      <input id="c-wheelbase"   type="number" step="0.01" value="0.20"></div>
      <div class="cfg-row"><label>track_width (m)</label>    <input id="c-track"       type="number" step="0.01" value="0.15"></div>
      <div class="cfg-row"><label>max_steer_deg</label>      <input id="c-maxsteer"    type="number" step="1"    value="30"></div>
      <div class="cfg-row"><label>max_speed_mps</label>      <input id="c-maxspeed"    type="number" step="0.05" value="0.5"></div>
      <div class="cfg-row"><label>max_wheel_ticks</label>    <input id="c-maxticks"    type="number" step="10"   value="300"></div>
      <div class="cfg-row"><label>steer_center_ticks</label> <input id="c-center"      type="number" step="1"    value="512"></div>
      <div class="cfg-row"><label>steer_dir [FL,FR,RL,RR]</label><input id="c-sdir" type="text" value="1,-1,-1,1"></div>
      <div class="cfg-row"><label>drive_dir [FL,FR,RL,RR]</label><input id="c-ddir" type="text" value="1,-1,1,-1"></div>
    </div>
    <div id="btn-row" style="margin-top:10px; display:flex; gap:8px;">
      <button class="btn btn-save" onclick="saveConfig()">💾 Save config</button>
      <button class="btn btn-load" onclick="loadConfig()">📂 Load config</button>
    </div>

    <!-- Live state from robot -->
    <div style="margin-top:14px;">
      <h3>Robot state <span style="font-size:11px; color:#555; font-weight:normal;">(auto-refreshes)</span></h3>
      <table>
        <tr><th>ID</th><th>Mode</th><th>Pos</th><th>Speed</th><th>Temp</th><th>Volt</th></tr>
        <tbody id="state-table"><tr><td colspan="6" style="color:#555; padding:6px;">—</td></tr></tbody>
      </table>
    </div>
  </div>

</div>

<div id="status-bar">Ready. Adjust sliders to preview — click "Send to robot" to drive.</div>

<script>
// ── Wheel positions in SVG space (x, y of wheel centre) ──────────────────────
const WHEEL_POS = {
  FL: { x: -65, y: -55 },
  FR: { x:  65, y: -55 },
  RL: { x: -65, y:  55 },
  RR: { x:  65, y:  55 },
};
const WHEEL_ORDER = ['FL','FR','RL','RR'];

// ── Read config from form ─────────────────────────────────────────────────────
function readConfig() {
  const parseDir = id => document.getElementById(id).value
    .split(',').map(v => parseInt(v.trim()));
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

// ── Populate config form ──────────────────────────────────────────────────────
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

// ── Sliders ───────────────────────────────────────────────────────────────────
const slSteer = document.getElementById('sl-steer');
const slSpeed = document.getElementById('sl-speed');

slSteer.addEventListener('input', () => {
  document.getElementById('lbl-steer').textContent = parseFloat(slSteer.value).toFixed(1);
  preview();
});
slSpeed.addEventListener('input', () => {
  document.getElementById('lbl-speed').textContent = parseFloat(slSpeed.value).toFixed(2);
  preview();
});

// Config inputs also trigger preview
document.querySelectorAll('.cfg-grid input').forEach(el => el.addEventListener('input', preview));

// ── Preview (compute only, no robot) ─────────────────────────────────────────
async function preview() {
  const body = {
    steer_deg: parseFloat(slSteer.value),
    speed_mps: parseFloat(slSpeed.value),
    config:    readConfig(),
  };
  try {
    const r = await fetch('/compute', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    const d = await r.json();
    updateViz(d);
  } catch(e) { setStatus('Compute error: ' + e, true); }
}

// ── Send to robot ─────────────────────────────────────────────────────────────
async function sendCmd() {
  const body = {
    steer_deg: parseFloat(slSteer.value),
    speed_mps: parseFloat(slSpeed.value),
    config:    readConfig(),
  };
  try {
    const r = await fetch('/send', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) { setStatus('Robot: ' + d.error, true); return; }
    setStatus('Sent → steer=' + body.steer_deg.toFixed(1) + '°  speed=' + body.speed_mps.toFixed(2) + ' m/s');
    updateViz(d);
  } catch(e) { setStatus('Send error: ' + e, true); }
}

async function sendEstop() {
  try {
    await fetch('/estop', { method: 'POST' });
    setStatus('E-STOP sent.');
  } catch(e) { setStatus('Estop error: ' + e, true); }
}

// ── Config save/load ──────────────────────────────────────────────────────────
async function saveConfig() {
  const cfg = readConfig();
  const r = await fetch('/config', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(cfg)
  });
  const d = await r.json();
  setStatus('Config saved to ' + d.saved);
}

async function loadConfig() {
  const r = await fetch('/config');
  const d = await r.json();
  fillConfig(d);
  setStatus('Config loaded.');
  preview();
}

// ── Update visualisation ──────────────────────────────────────────────────────
function updateViz(data) {
  const wheels = data.wheels;

  // Wheel table
  const tbody = document.getElementById('wheel-table');
  tbody.innerHTML = '';
  wheels.forEach(w => {
    const tr = document.createElement('tr');
    const dirColor = w.drive_dir === 'CCW' ? '#4f4' : w.drive_dir === 'CW' ? '#f84' : '#888';
    tr.innerHTML =
      `<td class="highlight">${w.label}</td>` +
      `<td>${w.steer_angle > 0 ? '+' : ''}${w.steer_angle.toFixed(1)}°</td>` +
      `<td>${w.steer_tick}</td>` +
      `<td style="color:${dirColor}">${w.drive_dir} ${w.drive_mag}</td>` +
      `<td style="color:#555">${w.drive_raw}</td>`;
    tbody.appendChild(tr);
  });

  // Turning radius + clamped steer
  document.getElementById('tr-val').textContent =
    data.turning_radius !== null ? data.turning_radius + ' m' : '∞ (straight)';
  document.getElementById('cs-val').textContent =
    (data.steer_clamped >= 0 ? '+' : '') + data.steer_clamped.toFixed(1);

  // SVG: rotate each wheel group
  WHEEL_ORDER.forEach((lbl, i) => {
    const w   = wheels[i];
    const pos = WHEEL_POS[lbl];
    const el  = document.getElementById('wheel-' + lbl);
    el.setAttribute('transform',
      `translate(${pos.x},${pos.y}) rotate(${w.steer_angle})`);

    // Speed arrow
    const arr = document.getElementById('arrow-' + lbl);
    arr.innerHTML = '';
    if (w.drive_mag > 0) {
      const maxMag = 300;
      const len    = Math.min(w.drive_mag / maxMag, 1) * 28 + 8;
      // CCW = forward (+y in wheel-local → -y in SVG = upward = forward for top view)
      const dir    = w.drive_dir === 'CCW' ? -1 : 1;
      const y2     = dir * len;
      const tipY   = y2 + (dir * 4);
      const color  = w.drive_dir === 'CCW' ? '#4f4' : '#f84';
      arr.innerHTML =
        `<g transform="translate(${pos.x},${pos.y}) rotate(${w.steer_angle})">` +
        `<line x1="0" y1="0" x2="0" y2="${y2}" stroke="${color}" stroke-width="2"/>` +
        `<polygon points="0,${tipY} -3,${y2} 3,${y2}" fill="${color}"/>` +
        `</g>`;
    }
  });

  // Rotation centre dot
  const rc    = document.getElementById('rc-dot');
  const rcLbl = document.getElementById('rc-label');
  if (data.turning_radius !== null) {
    const R      = data.turning_radius;
    const sign   = data.steer_clamped >= 0 ? 1 : -1;
    // RC is at (sign*R, 0) in robot frame; SVG: x=sign*R (right=positive), y=0
    const rcX    = sign * Math.min(R * 150, 180); // scale: 1m ≈ 150px, clamp
    rc.setAttribute('cx', rcX);
    rc.setAttribute('cy', 0);
    rc.setAttribute('opacity', 0.8);
    rcLbl.setAttribute('x', rcX + 7);
    rcLbl.setAttribute('y', 4);
    rcLbl.setAttribute('opacity', 0.8);
  } else {
    rc.setAttribute('opacity', 0);
    rcLbl.setAttribute('opacity', 0);
  }
}

// ── Robot state auto-refresh ──────────────────────────────────────────────────
async function refreshState() {
  try {
    const r = await fetch('/state');
    const d = await r.json();
    const badge = document.getElementById('conn-badge');

    if (!d.connected) {
      badge.textContent = '● not connected';
      badge.style.background = '#333'; badge.style.color = '#888';
      return;
    }
    badge.textContent = '● connected';
    badge.style.background = '#1a3a1a'; badge.style.color = '#4f4';

    const tbody = document.getElementById('state-table');
    if (!d.state) { tbody.innerHTML = '<tr><td colspan="6" style="color:#555">waiting...</td></tr>'; return; }

    tbody.innerHTML = d.state.servos.map(s => {
      if (!s.available) return `<tr><td>${s.id}</td><td colspan="5" style="color:#555">UNAVAIL</td></tr>`;
      return `<tr>
        <td>${s.id}</td><td>${s.mode}</td><td>${s.pos}</td>
        <td>${s.speed}</td><td>${s.temp_c}°C</td><td>${s.volt_v.toFixed(1)}V</td>
      </tr>`;
    }).join('');

    if (d.state.e_stop) setStatus('⚠ E-STOP active on robot', true);
  } catch(_) {}
}

// ── Status bar ────────────────────────────────────────────────────────────────
function setStatus(msg, err=false) {
  const el = document.getElementById('status-bar');
  el.textContent = msg;
  el.className = err ? 'err' : '';
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadConfig();
setInterval(preview, 200);        // re-preview if config inputs change
setInterval(refreshState, 800);   // poll robot state
</script>
</body>
</html>
"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _driver

    parser = argparse.ArgumentParser(description='Ackermann config UI server')
    parser.add_argument('--port',    '-p', default=None,  help='Serial port (optional)')
    parser.add_argument('--baud',    '-b', type=int, default=115200)
    parser.add_argument('--ui-port', '-u', type=int, default=8080, help='HTTP port (default 8080)')
    args = parser.parse_args()

    if args.port:
        port = args.port
    else:
        candidates = (
            glob.glob('/dev/opencm')
            + glob.glob('/dev/serial/by-id/*ROBOTIS*')
            + sorted(glob.glob('/dev/ttyACM*'))
        )
        port = candidates[0] if candidates else None

    if port:
        print(f'Connecting to robot on {port}...')
        _driver = SerialDriver(port, args.baud)
        _driver.connect()
        _driver.start()
        print('Robot connected.')
    else:
        print('No serial port found — running in simulation mode (no robot).')

    print(f'Open:  http://localhost:{args.ui_port}')
    uvicorn.run(app, host='0.0.0.0', port=args.ui_port, log_level='warning')


if __name__ == '__main__':
    main()

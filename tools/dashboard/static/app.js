var _keys         = { w: false, a: false, s: false, d: false };
var _maxSteer     = 30.0;
var _driving      = false;
var _estopLatched = false;   // when true, the drive merger refuses to send frames
var _cfg          = null;
var _driveMode        = 'wasd';   // overwritten from config on first loadConfig()
var _modeInitialised  = false;    // prevents the 5 s re-poll from resetting an in-session toggle
var _touch            = { active: false, steer: 0, throttle: 0 };
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
      // Apply default drive mode from config only on first load so the 5 s
      // re-poll doesn't reset an in-session toggle.
      if (!_modeInitialised) {
        _modeInitialised = true;
        setDriveMode(_cfg.default_drive_mode || 'wasd');
      }
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
  if (d.state.e_stop) {
    _driving = false;
    if (!_estopLatched) { _estopLatched = true; updateEstopButton(); }
  }

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
    // WASD keys are inert in touchpad mode (keys are also hidden)
    if (_driveMode !== 'wasd') return;
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

// Drive merger — 10 Hz.
//   Manual source depends on drive mode:
//     WASD mode     → Shift deadman wins (Shift + W/A/S/D)
//     Touchpad mode → pointer-held deadman wins (_touch.active)
//   Fallback: gamepad (L1 deadman)
// While any deadman is held, frames flow every 100 ms so the firmware
// watchdog keeps the robot live. Releasing all deadmen sends one final
// 0,0 frame; the watchdog then brakes the robot.
setInterval(function() {
  // Latched e-stop: skip the loop entirely so the firmware watchdog
  // brakes the robot and stays braked. Release with the RESET button.
  if (_estopLatched) {
    updateGauges(0, 0);
    return;
  }

  var active = false;
  var steer = 0, speed = 0;

  if (_driveMode === 'wasd' && _shiftHeld) {
    if (_keys.a) steer -= _maxSteer;
    if (_keys.d) steer += _maxSteer;
    if (_keys.w) speed += 1.0;
    if (_keys.s) speed -= 1.0;
    active = true;
  } else if (_driveMode === 'touchpad' && _touch.active) {
    steer  = _touch.steer    * _maxSteer;
    speed  = _touch.throttle;
    active = true;
  } else if (_gp.connected && _gp.deadman) {
    // Robot BT gamepad (evdev) — wins over PC gamepad; local operator overrides remote
    steer = _gp.steer * _maxSteer;
    speed = _gp.throttle;
    active = true;
  } else if (_pcGp.connected && _pcGp.deadman) {
    // PC gamepad (Browser Gamepad API) — last fallback
    steer = _pcGp.steer * _maxSteer;
    speed = _pcGp.throttle;
    active = true;
  }

  // Read PC gamepad every tick (Browser Gamepad API is poll-only)
  _readPcGamepad();
  updatePcGamepadWidget();

  updateGauges(steer, speed);
  if (active) {
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

// Latching is instant (safety). Unlatching requires a 1 s hold via the
// button or the LS+RS gamepad combo (see hold state machine below).
function latchEstop() {
  if (_estopLatched) return;
  _estopLatched = true;
  _driving = false;
  _keys = { w:false, a:false, s:false, d:false };
  if (_touch.active) _touchRelease();
  updateKeyDisplay(); updateGauges(0, 0);
  updateEstopButton();
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/estop');
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = function() {};
  xhr.send('{}');
}

function unlatchEstop() {
  if (!_estopLatched) return;
  _estopLatched = false;
  updateEstopButton();
}

// Backwards-compat: existing call sites (spacebar, etc.) still work.
function doEstop() { latchEstop(); }

function updateEstopButton() {
  var btn = document.getElementById('btn-estop-main');
  if (!btn) return;
  if (_estopLatched) {
    btn.textContent = '↻ HOLD TO RESET';
    btn.style.background = '#a86010';
  } else {
    btn.innerHTML   = '&#9632; E-STOP';
    btn.style.background = '';
  }
}

// ── Hold-to-confirm unlatch (button + LS+RS gamepad combo) ────────────────────
// Both input paths drive the same 1 s timer. Whichever starts first wins.
// A visual fill on the button shows hold progress for both sources.
var _holdSource = null;   // 'button' | 'gamepad' | null
var _holdStart  = 0;

function startHold(source) {
  if (!_estopLatched) return;     // only meaningful while latched
  if (_holdSource) return;        // already counting
  _holdSource = source;
  _holdStart  = Date.now();
  var btn = document.getElementById('btn-estop-main');
  if (btn) btn.classList.add('holding');
}

function cancelHold(source) {
  if (_holdSource !== source) return;
  _holdSource = null;
  var btn = document.getElementById('btn-estop-main');
  if (btn) btn.classList.remove('holding');
}

setInterval(function() {
  if (!_holdSource) return;
  if (Date.now() - _holdStart >= 1000) {
    var src = _holdSource;
    cancelHold(src);
    unlatchEstop();
  }
}, 50);

// Wire the E-STOP button: press-down latches when red; press-and-hold
// resets when amber. mouseleave / touchcancel abort the hold cleanly.
(function attachEstopButton() {
  var btn = document.getElementById('btn-estop-main');
  if (!btn) return;
  function down(e) {
    e.preventDefault();
    if (_estopLatched) startHold('button');
    else               latchEstop();
  }
  function up() { cancelHold('button'); }
  btn.addEventListener('mousedown',  down);
  btn.addEventListener('mouseup',    up);
  btn.addEventListener('mouseleave', up);
  btn.addEventListener('touchstart', down);
  btn.addEventListener('touchend',   up);
  btn.addEventListener('touchcancel',up);
})();

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

function setupCameraReconnect() {
  var wrap        = document.getElementById('cam-wrap');
  var placeholder = document.getElementById('cam-placeholder');
  var _healthy    = true;
  var _imgStyle   = document.getElementById('cam-img').style.cssText;

  function setHealthy() {
    if (_healthy) return;
    _healthy = true;
    // Replace the img element entirely — guarantees a fresh browser request
    var old = document.getElementById('cam-img');
    var img = document.createElement('img');
    img.id            = 'cam-img';
    img.style.cssText = _imgStyle;
    img.src           = '/camera?' + Date.now();
    wrap.insertBefore(img, old);
    old.src = '';
    wrap.removeChild(old);
    placeholder.style.display = 'none';
    img.style.display         = 'block';
  }

  function setUnhealthy() {
    if (!_healthy) return;
    _healthy = false;
    var img           = document.getElementById('cam-img');
    img.src           = '';
    img.style.display = 'none';
    placeholder.style.display = 'block';
  }

  setInterval(function() {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/camera/ping');
    xhr.timeout = 1500;
    xhr.onload    = function() { if (xhr.status === 200) setHealthy(); else setUnhealthy(); };
    xhr.onerror   = function() { setUnhealthy(); };
    xhr.ontimeout = function() { setUnhealthy(); };
    xhr.send();
  }, 2000);
}

setupCameraReconnect();


// ── Drive mode (WASD ↔ Touchpad) ─────────────────────────────────────────────

var _HINTS = {
  wasd:     ['<div><span style="color:#7cf;font-weight:bold;">⇧+W/S</span>  fwd / rev</div>',
             '<div><span style="color:#7cf;font-weight:bold;">⇧+A/D</span>  steer</div>',
             '<div><span style="color:#7cf;font-weight:bold;">Spc/Esc</span>  e-stop</div>'].join(''),
  touchpad: ['<div><span style="color:#7cf;font-weight:bold;">drag up</span>  fwd</div>',
             '<div><span style="color:#7cf;font-weight:bold;">drag right</span>  steer</div>',
             '<div><span style="color:#7cf;font-weight:bold;">Spc/Esc</span>  e-stop</div>'].join('')
};

function setDriveMode(mode) {
  // if switching while a touch is active, release it first so no drift
  if (_touch.active) _touchRelease();

  _driveMode = mode;

  document.getElementById('mode-wasd').classList.toggle('mode-hidden', mode !== 'wasd');
  document.getElementById('mode-touchpad').classList.toggle('mode-hidden', mode !== 'touchpad');
  document.getElementById('mode-btn-wasd').classList.toggle('mode-btn-active', mode === 'wasd');
  document.getElementById('mode-btn-touchpad').classList.toggle('mode-btn-active', mode === 'touchpad');

  document.getElementById('drive-hints').innerHTML = _HINTS[mode];

  var ts = document.getElementById('topstatus');
  if (mode === 'wasd') {
    ts.textContent = 'Hold ⇧ + WASD to drive or L1 + sticks (gamepad).';
  } else {
    ts.textContent = 'Press & drag the touchpad to drive or L1 + sticks (gamepad).';
  }
}

// Drive mode is applied on first loadConfig() callback (from config default).
// setDriveMode() here would race with the XHR — don't call it at module load.

// ── Touchpad pointer handlers ─────────────────────────────────────────────────

var _TP_R = 74;   // usable radius (pad half-width minus dot radius)
var _TP_DEAD = 6; // centre deadzone in px

function _touchSteerThrottle(padX, padY) {
  var dx = Math.max(-_TP_R, Math.min(_TP_R, padX));
  var dy = Math.max(-_TP_R, Math.min(_TP_R, padY));
  var steer    = Math.abs(dx) < _TP_DEAD ? 0 : dx / _TP_R;
  var throttle = Math.abs(dy) < _TP_DEAD ? 0 : -dy / _TP_R;
  return { steer: steer, throttle: throttle, dx: dx, dy: dy };
}

function _touchRelease() {
  _touch.active   = false;
  _touch.steer    = 0;
  _touch.throttle = 0;
  var pad = document.getElementById('drive-touchpad');
  if (pad) pad.classList.remove('touchpad-engaged');
  var dot = document.getElementById('tp-dot');
  if (dot) { dot.setAttribute('cx', 0); dot.setAttribute('cy', 0); }
}

(function wireTouchpad() {
  var pad = document.getElementById('drive-touchpad');
  if (!pad) return;
  var rect;

  function padCoords(e) {
    if (!rect) rect = pad.getBoundingClientRect();
    // SVG viewBox is -80..-80..160..160 so centre at (rect.width/2, rect.height/2)
    var cx = rect.left + rect.width  / 2;
    var cy = rect.top  + rect.height / 2;
    var scale = rect.width / 160;
    return { x: (e.clientX - cx) / scale, y: (e.clientY - cy) / scale };
  }

  pad.addEventListener('pointerdown', function(e) {
    if (e.button !== undefined && e.button !== 0) return; // left / touch only
    if (_estopLatched) return;
    e.preventDefault();
    pad.setPointerCapture(e.pointerId);
    rect = pad.getBoundingClientRect();
    var c = padCoords(e);
    var r = _touchSteerThrottle(c.x, c.y);
    _touch.active   = true;
    _touch.steer    = r.steer;
    _touch.throttle = r.throttle;
    pad.classList.add('touchpad-engaged');
    var dot = document.getElementById('tp-dot');
    if (dot) { dot.setAttribute('cx', r.dx); dot.setAttribute('cy', r.dy); }
  });

  pad.addEventListener('pointermove', function(e) {
    if (!_touch.active) return;
    e.preventDefault();
    var c = padCoords(e);
    var r = _touchSteerThrottle(c.x, c.y);
    _touch.steer    = r.steer;
    _touch.throttle = r.throttle;
    var dot = document.getElementById('tp-dot');
    if (dot) { dot.setAttribute('cx', r.dx); dot.setAttribute('cy', r.dy); }
  });

  function onRelease(e) {
    if (!_touch.active) return;
    e.preventDefault();
    _touchRelease();
  }
  pad.addEventListener('pointerup',     onRelease);
  pad.addEventListener('pointercancel', onRelease);
})();


// ── Gamepad live state + input-source pill ────────────────────────────────────
// Polls /api/gamepad/state at 20 Hz, updates the topbar pill and the
// gamepad card. Shift tracking is for the pill only — actual drive-source
// switching happens in Phase E.

var _gp   = { connected: false, name: '', steer: 0, throttle: 0, deadman: false };
var _pcGp = { connected: false, name: '', steer: 0, throttle: 0, deadman: false };
var _gpEstopComboPrev = false;
var _shiftHeld = false;

window.addEventListener('keydown', function(e) {
  if (e.key === 'Shift') _shiftHeld = true;
});
window.addEventListener('keyup', function(e) {
  if (e.key === 'Shift') _shiftHeld = false;
});
window.addEventListener('blur', function() { _shiftHeld = false; });

function setBipolarBar(elId, value, posColor, negColor) {
  var el = document.getElementById(elId);
  var v = Math.max(-1, Math.min(1, value));
  if (v >= 0) {
    el.style.left = '50%';
    el.style.width = (v * 50) + '%';
    el.style.background = posColor;
  } else {
    el.style.left = (50 + v * 50) + '%';
    el.style.width = (-v * 50) + '%';
    el.style.background = negColor;
  }
}

function updateInputPill() {
  var pill = document.getElementById('input-pill');
  // Manual sources take precedence by mode
  if (_driveMode === 'wasd' && _shiftHeld) {
    pill.textContent = 'KEYBOARD';
    pill.className   = 'keyboard';
    return;
  }
  if (_driveMode === 'touchpad' && _touch.active) {
    pill.textContent = 'TOUCHPAD';
    pill.className   = 'keyboard';
    return;
  }
  if (_gp.connected && _gp.deadman) {
    pill.textContent = 'GAMEPAD: ' + (_gp.name || '?');
    pill.className   = 'gamepad';
    return;
  }
  if (_pcGp.connected && _pcGp.deadman) {
    pill.textContent = 'GAMEPAD-PC';
    pill.className   = 'gamepad';
    return;
  }
  if (!_gp.connected && !_pcGp.connected) {
    pill.textContent = 'GAMEPAD: NONE';
    pill.className   = 'disconnect';
  } else {
    pill.textContent = 'IDLE';
    pill.className   = 'idle';
  }
}

function updateGamepadWidget() {
  var dc = document.getElementById('gp-disconnected');
  var co = document.getElementById('gp-connected');
  if (!_gp.connected) {
    dc.style.display = 'block';
    co.style.display = 'none';
    return;
  }
  dc.style.display = 'none';
  co.style.display = 'block';
  document.getElementById('gp-name').textContent = _gp.name || '?';

  // Stick dot — steer drives X, throttle drives Y (flip Y so fwd = up)
  var dot = document.getElementById('gp-stick-dot');
  dot.setAttribute('cx', Math.max(-1, Math.min(1, _gp.steer))    *  40);
  dot.setAttribute('cy', Math.max(-1, Math.min(1, _gp.throttle)) * -40);

  setBipolarBar('gp-bar-steer',    _gp.steer,    '#7cf', '#f84');
  setBipolarBar('gp-bar-throttle', _gp.throttle, '#4fa', '#f84');

  var fmt = function(v) { return (v >= 0 ? '+' : '') + v.toFixed(2); };
  document.getElementById('gp-val-steer').textContent    = fmt(_gp.steer);
  document.getElementById('gp-val-throttle').textContent = fmt(_gp.throttle);
}

function pollGamepad() {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/api/gamepad/state');
  xhr.timeout = 400;
  xhr.onload = function() {
    if (xhr.status === 200) {
      try {
        var d = JSON.parse(xhr.responseText);
        _gp.connected = !!d.connected;
        _gp.name      = d.name     || '';
        _gp.steer     = +d.steer   || 0;
        _gp.throttle  = +d.throttle || 0;
        _gp.deadman   = !!d.deadman;

        // L1 + D-pad diagonal → instant latch (rising edge).
        // LS + RS held         → drive the hold-to-confirm timer.
        var estopRise = !!d.estop_combo && !_gpEstopComboPrev;
        _gpEstopComboPrev = !!d.estop_combo;
        if (estopRise) latchEstop();

        if (d.rearm_combo) startHold('gamepad');
        else               cancelHold('gamepad');
      } catch (e) { _gp.connected = false; }
    } else {
      _gp.connected = false;
    }
    updateInputPill();
    updateGamepadWidget();
  };
  xhr.onerror = xhr.ontimeout = function() {
    _gp.connected = false;
    updateInputPill();
    updateGamepadWidget();
  };
  xhr.send();
}

// ── PC Gamepad (Browser Gamepad API) ─────────────────────────────────────────
// Polled in the 10 Hz drive loop; no separate interval needed.
// W3C Standard Gamepad mapping: axes[0]=LX, axes[1]=LY, buttons[6]=L1/LB.
// Y-axis is inverted (up = −1) so we negate it for throttle.

function _readPcGamepad() {
  var gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
  var gp = null;
  for (var i = 0; i < gamepads.length; i++) {
    if (gamepads[i] && gamepads[i].connected) { gp = gamepads[i]; break; }
  }
  if (!gp) {
    _pcGp.connected = false;
    _pcGp.deadman   = false;
    _pcGp.steer     = 0;
    _pcGp.throttle  = 0;
    _pcGp.name      = '';
    return;
  }
  var DEAD = 0.08;
  var ax = gp.axes[0] || 0;
  var ay = gp.axes[1] || 0;
  _pcGp.connected = true;
  _pcGp.name      = gp.id || 'Gamepad';
  _pcGp.steer     = Math.abs(ax) > DEAD ? ax : 0;
  _pcGp.throttle  = Math.abs(ay) > DEAD ? -ay : 0;  // invert Y: up = +throttle
  _pcGp.deadman   = !!(gp.buttons[6] && gp.buttons[6].pressed) ||
                    !!(gp.buttons[4] && gp.buttons[4].pressed);  // L1 or LB
}

function updatePcGamepadWidget() {
  var dc = document.getElementById('pc-gp-disconnected');
  var co = document.getElementById('pc-gp-connected');
  if (!_pcGp.connected) {
    dc.style.display = 'block';
    co.style.display = 'none';
    return;
  }
  dc.style.display = 'none';
  co.style.display = 'block';
  document.getElementById('pc-gp-name').textContent = _pcGp.name;
}


// The topbar pill is a pure state indicator now. Pairing lives on its own
// dedicated button so the affordance is obvious and stays available even
// while a controller is connected (pair/swap any time).
document.getElementById('btn-pair-gamepad').addEventListener('click', function() {
  if (window.GamepadSetup) window.GamepadSetup.open();
});

setInterval(pollGamepad, 50);   // 20 Hz
pollGamepad();

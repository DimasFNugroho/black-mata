// setup_modal.js — Bluetooth gamepad pairing modal.
//
// Lazily loads setup_modal.html on first open, then drives the pairing flow
// entirely through the backend:
//   POST /api/gamepad/setup/start         begin
//   GET  /api/gamepad/setup/events  (SSE) progress / prompts / result
//   POST /api/gamepad/setup/continue      operator put controller in pairing mode
//   POST /api/gamepad/setup/select {mac}  operator picked a device
//   POST /api/gamepad/setup/cancel        abort
//
// Exposed as window.GamepadSetup.open() — wired to the "+ Pair Controller"
// button in the Gamepad card (see app.js).

(function () {
  var loaded = false;       // modal HTML injected yet?
  var es     = null;        // EventSource for the progress stream
  var errored = false;      // did the flow report an error?
  var okName  = null;       // controller name from a successful verify

  function $(id) { return document.getElementById(id); }

  function post(path, body) {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.send(body ? JSON.stringify(body) : '{}');
  }

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function setStatus(t) { $('setup-status').textContent = t; }
  function setBar(pct)  { $('setup-bar').style.width =
                            Math.max(0, Math.min(100, pct)) + '%'; }
  function appendLog(m) {
    var el = $('setup-log');
    el.textContent += (el.textContent ? '\n' : '') + m;
    el.scrollTop = el.scrollHeight;
  }
  function showSection(id) {
    ['setup-pairing-prompt', 'setup-devices', 'setup-result']
      .forEach(function (s) { $(s).style.display = (s === id ? 'block' : 'none'); });
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  function loadModal(cb) {
    if (loaded) { cb(); return; }
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/static/setup_modal.html');
    xhr.onload = function () {
      if (xhr.status !== 200) return;
      var root = document.createElement('div');
      root.innerHTML = xhr.responseText;
      document.body.appendChild(root);
      wire();
      loaded = true;
      cb();
    };
    xhr.send();
  }

  function wire() {
    $('setup-continue').addEventListener('click', function () {
      post('/api/gamepad/setup/continue');
      showSection(null);
      setStatus('Scanning for controllers…');
    });
    $('setup-cancel').addEventListener('click', close);
    $('setup-close').addEventListener('click', close);
    $('setup-overlay').addEventListener('click', function (e) {
      if (e.target === $('setup-overlay')) close();   // click the backdrop
    });
  }

  function resetUI() {
    errored = false; okName = null;
    setStatus('Starting…'); setBar(0);
    $('setup-log').textContent = '';
    $('setup-result').textContent = '';
    showSection(null);
    $('setup-close').style.display  = 'none';
    $('setup-cancel').style.display = '';
  }

  function open() {
    loadModal(function () {
      $('setup-overlay').style.display = 'flex';
      resetUI();
      post('/api/gamepad/setup/start');
      es = new EventSource('/api/gamepad/setup/events');
      es.onmessage = function (e) { handle(JSON.parse(e.data)); };
      es.onerror   = function () { /* stream closed by server; ignore */ };
    });
  }

  function close() {
    if (es) { es.close(); es = null; }
    post('/api/gamepad/setup/cancel');
    if (loaded) $('setup-overlay').style.display = 'none';
  }

  // ── Event handling ─────────────────────────────────────────────────────────
  function handle(ev) {
    switch (ev.kind) {
      case 'phase':      setStatus(ev.label); setBar(0); break;
      case 'phase_step': setStatus(ev.step); break;
      case 'progress':   if (ev.total) setBar(ev.elapsed / ev.total * 100); break;
      case 'log':        appendLog(ev.message); break;
      case 'await':
        if (ev.what === 'pairing_mode') {
          setStatus('Ready to pair'); setBar(0);
          showSection('setup-pairing-prompt');
        }
        break;
      case 'devices':    renderDevices(ev.devices); break;
      case 'done':       if (ev.name) okName = ev.name; break;  // verify success
      case 'error':      errored = true; finishError(ev.message); break;
      case 'complete':   if (!errored) finishSuccess(); if (es) es.close(); break;
      case 'aborted':    if (es) es.close(); break;
    }
  }

  function renderDevices(devices) {
    setStatus('Select your controller');
    var list = $('setup-device-list');
    list.innerHTML = '';
    (devices || []).forEach(function (d) {
      var b = document.createElement('button');
      b.className = 'btn setup-device';
      b.textContent = d.name + '  (' + d.mac + ')';
      b.addEventListener('click', function () {
        post('/api/gamepad/setup/select', { mac: d.mac });
        showSection(null);
        setStatus('Pairing with ' + d.name + '…');
      });
      list.appendChild(b);
    });
    showSection('setup-devices');
  }

  function finishSuccess() {
    setStatus('Done');
    setBar(100);
    var r = $('setup-result');
    r.className = 'setup-section setup-ok';
    r.textContent = okName
      ? ('Connected: ' + okName + '. You can close this window.')
      : 'Pairing complete. You can close this window.';
    showSection('setup-result');
    $('setup-cancel').style.display = 'none';
    $('setup-close').style.display  = '';
  }

  function finishError(msg) {
    setStatus('Setup failed');
    var r = $('setup-result');
    r.className = 'setup-section setup-err';
    r.textContent = msg || 'Pairing failed. Please try again.';
    showSection('setup-result');
    $('setup-cancel').style.display = 'none';
    $('setup-close').style.display  = '';
  }

  window.GamepadSetup = { open: open };
})();

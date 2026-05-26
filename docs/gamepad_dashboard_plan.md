# Gamepad ↔ Dashboard Integration — Plan

Integrate the paired Bluetooth controller with the operator drive dashboard
(`tools/dashboard/server.py`). Show live gamepad state, drive via sticks,
manage Bluetooth pairing from a modal, and synchronise E-stop between the
controller and the UI.

---

## Mental Model

The dashboard already has hold-to-drive semantics via WASD: holding a key
sends drive frames, releasing it stops them. The gamepad is just another
input layer with the same shape:

- Stick past deadzone → drive frames flow
- Stick centered → no frames

There is **no** "arm / disarm" mode. The "arm" concept in `gamepad_test.py`
was bolted on for the standalone tool; the dashboard does not need it.

---

## Input Precedence — Shift-keyboard, default-gamepad

| Shift held | WASD pressed | Gamepad sticks | Drives the robot |
|------------|--------------|----------------|------------------|
| yes        | yes          | —              | **Keyboard**     |
| yes        | no           | —              | nothing          |
| no         | —            | past deadzone  | **Gamepad**      |
| no         | —            | centered       | nothing          |

Shift is the keyboard's deadman. Releasing Shift hands the wheel back to the
gamepad. Pressing W/A/S/D without Shift does not drive the robot.

Note: Shift+key combinations may need `preventDefault()` to avoid browser
shortcuts (text selection, etc.).

---

## E-stop Unlatch

Unlatching must be deliberate. Two equivalent paths:

- **UI:** an amber `RESET E-STOP` button appears in the topbar only while
  the firmware reports e-stop latched. Hold-to-confirm: must be held for
  ~1 second with a visible fill animation. Releasing early cancels.
- **Gamepad:** LS + RS clicked simultaneously for ~1 second (same
  hold-to-confirm shape).

Both paths POST to a new `/api/estop/unlatch` endpoint, which clears the
server-side latch and lets the next drive frame through.

---

## File Layout

Split the dashboard into Python (logic) + static files (UI). The current
single-file `server.py` is already 863 lines; the gamepad work adds enough
HTML/CSS/JS that a triple-string in Python becomes painful.

```
tools/dashboard/
├── server.py                ← HTTP handlers, evdev reader, drive merger
└── static/
    ├── index.html           ← main dashboard markup
    ├── style.css            ← all styling
    ├── app.js               ← drive loop, polling, key handlers
    ├── gamepad.js           ← gamepad widget + setup modal logic
    └── setup_modal.html     ← modal markup (loaded lazily)
```

`server.py` gains a generic `/static/*` handler. `do_GET('/')` reads
`static/index.html` from disk on each request (no caching) so editing HTML
during development needs only a browser reload.

```
tools/gamepad/
├── gamepad_core.py          ← extracted from gamepad_test.py
│                              (GamepadState, normalize_*, MAP, calibration)
├── gamepad_test.py          ← unchanged behaviour, now imports from core
├── bt_setup.py              ← Python orchestrator for BT pairing
│                              (port of setup_gamepad.sh logic)
└── setup_gamepad.sh         ← thin wrapper around bt_setup.py
                               (terminal path keeps working)
```

The reason to port `setup_gamepad.sh` into Python: the dashboard popup
needs structured progress events (scan phase, device list, pair phase,
verify), not a stream of shell output. Both the terminal user and the
dashboard call the same `bt_setup.py` functions.

---

## Backend Endpoints

| Method | Path                              | Purpose                                              |
|--------|-----------------------------------|------------------------------------------------------|
| GET    | `/api/gamepad/state`              | live `{connected, name, steer, throttle, buttons}`   |
| GET    | `/api/gamepad/setup/events`       | SSE stream of pairing progress                       |
| POST   | `/api/gamepad/setup/start`        | begin pairing flow (scan adapter, ERTM check)        |
| POST   | `/api/gamepad/setup/continue`     | user acknowledged "controller in pairing mode"       |
| POST   | `/api/gamepad/setup/select`       | `{mac}` — pair the device the user picked            |
| POST   | `/api/gamepad/setup/cancel`       | abort pairing                                        |
| POST   | `/api/estop/unlatch`              | clear the latched e-stop                             |

---

## Phases

### Phase A — Refactor gamepad_test.py
- [ ] Extract `tools/gamepad/gamepad_core.py` (GamepadState, normalize_*, MAP, calibration loaders)
- [ ] `gamepad_test.py` imports from core; behaviour unchanged
- [ ] Verify `python3 tools/gamepad/gamepad_test.py` still works end-to-end

### Phase B — Split dashboard into static files
- [ ] Move HTML/CSS/JS out of `HTML_TEMPLATE` into `tools/dashboard/static/`
- [ ] Add `/static/*` handler in `server.py`
- [ ] Verify dashboard still works exactly as before

### Phase C — Backend gamepad reader
- [ ] `GamepadReader` background thread in `server.py` (uses `gamepad_core.py`)
- [ ] `/api/gamepad/state` endpoint
- [ ] Auto-reconnect when device disappears/reappears

### Phase D — Topbar pill + live widget
- [ ] Topbar pill: `KEYBOARD` / `GAMEPAD: <name>` / `IDLE` / `GAMEPAD: NONE`
- [ ] Click pill → opens setup modal
- [ ] Small gamepad widget (stick positions + throttle bar) under WASD card
- [ ] Browser polls `/api/gamepad/state` at 20 Hz

### Phase E — Drive merger + Shift precedence
- [ ] Browser tracks Shift state and stick activity
- [ ] Drive POST uses keyboard inputs when Shift held, gamepad otherwise
- [ ] `preventDefault()` on Shift+W/A/S/D to suppress browser shortcuts

### Phase F — E-stop unlatch UX
- [ ] `RESET E-STOP` button in topbar, visible only when latched
- [ ] Hold-to-confirm with visual fill animation
- [ ] LS + RS gamepad combo with same hold-to-confirm timing
- [ ] `/api/estop/unlatch` endpoint clears latch + acknowledges

### Phase G — BT setup orchestrator
- [ ] `tools/gamepad/bt_setup.py` — functions: `check_adapter`, `scan(secs)`, `pair(mac)`, `verify(mac)`
- [ ] Each function yields structured progress events (phase, percent, label)
- [ ] `setup_gamepad.sh` becomes a thin wrapper that consumes the same events and renders them as the existing progress bars

### Phase H — Setup modal UI
- [ ] Modal markup in `static/setup_modal.html`, loaded on demand
- [ ] Phases mirror the shell script: adapter check → ERTM → pairing-mode prompt → scan with progress bar → device picker → pair with phase progress bar → verify
- [ ] Consumes the SSE stream from `/api/gamepad/setup/events`
- [ ] Cancel button at any phase

---

## Open Items (resolved)

1. ~~Naming: arm / disarm replacement~~ → No mode needed; sticks-past-deadzone is the active state.
2. ~~WASD vs gamepad precedence~~ → Shift held = keyboard, otherwise gamepad.
3. ~~Best e-stop unlatch UX~~ → Hold-to-confirm button (UI) + LS+RS chord (gamepad), both 1 s hold.
4. ~~Modal embedded or split~~ → Split: dashboard becomes static files + `server.py`.

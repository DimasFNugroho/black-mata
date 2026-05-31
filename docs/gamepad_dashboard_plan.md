# Gamepad ↔ Dashboard Integration — Plan

Integrate the paired Bluetooth controller with the operator drive dashboard
(`tools/dashboard/server.py`). Show live gamepad state, drive via sticks,
manage Bluetooth pairing from a modal, and synchronise E-stop between the
controller and the UI.

---

## Mental Model

Both keyboard and gamepad use an explicit **deadman button**:

- **Shift** = keyboard's deadman. Drive frames flow while Shift + W/A/S/D
  are held; release Shift and nothing moves.
- **L1** = gamepad's deadman. Drive frames flow while L1 + sticks are
  active; release L1 and nothing moves.

This symmetry means at rest (no Shift, no L1) the robot never receives
drive commands no matter what's happening on the inputs.

---

## Input Precedence — Shift wins, then L1, otherwise idle

| Shift held | WASD pressed | L1 held | Gamepad sticks | Drives the robot |
|------------|--------------|---------|----------------|------------------|
| yes        | yes          | —       | —              | **Keyboard**     |
| yes        | no           | —       | —              | nothing          |
| no         | —            | yes     | any            | **Gamepad**      |
| no         | —            | no      | —              | nothing          |

Shift wins if both deadmen are held simultaneously, so the user always has
a clear way to take keyboard control even with the controller in hand.

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

**As built:** the latch currently lives client-side in `app.js` (`_estopLatched`).
Latching POSTs `/estop`; unlatching (after the 1 s hold) simply resumes drive
frames and lets the firmware watchdog settle. There is **no** server-side
`/api/estop/unlatch` endpoint yet.

**TODO (server-side hardening):** add `POST /api/estop/unlatch` so the latch is
enforced on the server too, not just in the browser — a reloaded/second tab
should not be able to drive while latched. Tracked under Phase F below.

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

| Method | Path                              | Purpose                                              | Status |
|--------|-----------------------------------|------------------------------------------------------|--------|
| GET    | `/api/gamepad/state`              | live `{connected, name, steer, throttle, buttons}`   | ✅ done |
| GET    | `/api/gamepad/setup/events`       | SSE stream of pairing progress                       | ⬜ TODO (Phase H) |
| POST   | `/api/gamepad/setup/start`        | begin pairing flow (scan adapter, ERTM check)        | ⬜ TODO (Phase H) |
| POST   | `/api/gamepad/setup/continue`     | user acknowledged "controller in pairing mode"       | ⬜ TODO (Phase H) |
| POST   | `/api/gamepad/setup/select`       | `{mac}` — pair the device the user picked            | ⬜ TODO (Phase H) |
| POST   | `/api/gamepad/setup/cancel`       | abort pairing                                        | ⬜ TODO (Phase H) |
| POST   | `/api/estop/unlatch`              | clear the latched e-stop (server-side)               | ⬜ TODO (latch is client-side for now) |

---

## Phases

### Phase A — Refactor gamepad_test.py ✅
- [x] Extract `tools/gamepad/gamepad_core.py` (GamepadState, normalize_*, MAP, calibration loaders)
- [x] `gamepad_test.py` imports from core; behaviour unchanged
- [x] Verify `python3 tools/gamepad/gamepad_test.py` still works end-to-end

### Phase B — Split dashboard into static files ✅
- [x] Move HTML/CSS/JS out of `HTML_TEMPLATE` into `tools/dashboard/static/`
      (shipped as `index.html` + `app.js` + `style.css`; no separate `gamepad.js`)
- [x] Add `/static/*` handler in `server.py`
- [x] Verify dashboard still works exactly as before

### Phase C — Backend gamepad reader ✅
- [x] `GamepadReader` background thread in `server.py` (uses `gamepad_core.py`)
- [x] `/api/gamepad/state` endpoint
- [x] Auto-reconnect when device disappears/reappears

### Phase D — Topbar pill + live widget ✅
- [x] Topbar pill: `KEYBOARD` / `GAMEPAD: <name>` / `IDLE` / `GAMEPAD: NONE`
- [~] Click pill → opens setup modal *(pill present; modal is Phase H, not yet wired)*
- [x] Small gamepad widget (stick positions + throttle bar) under WASD card
- [x] Browser polls `/api/gamepad/state` at 20 Hz

### Phase E — Drive merger + Shift precedence ✅
- [x] Browser tracks Shift state and stick activity
- [x] Drive POST uses keyboard inputs when Shift held, gamepad otherwise
- [x] `preventDefault()` on Shift+W/A/S/D to suppress browser shortcuts

### Phase F — E-stop unlatch UX ✅ (UX done; server endpoint deferred)
- [x] `RESET E-STOP` button in topbar, visible only when latched
- [x] Hold-to-confirm with visual fill animation
- [x] LS + RS gamepad combo with same hold-to-confirm timing
- [ ] `/api/estop/unlatch` endpoint clears latch + acknowledges
      *(TODO — latch is currently client-side in `app.js`; see "E-stop Unlatch" above)*

### Phase G — BT setup orchestrator ✅
- [x] `tools/gamepad/bt_setup.py` — functions yield structured progress events;
      hardened to match the old shell's BlueZ workarounds (connect retry loop,
      `timeout`-wrapped bluetoothctl calls, rfkill/power-on/verify, paired-check)
- [x] `setup_gamepad.sh` is now a thin wrapper that execs `bt_setup.py`
      (which renders the progress bars), so pairing logic lives in one place
- [x] Verified end-to-end on the Jetson + RTL8761B dongle with an Xbox Wireless
      Controller. Key lessons baked in: the whole flow must run in ONE
      bluetoothctl session (cross-session purge made `pair` fail), cache must be
      cleared with `remove '*'` (not `remove <MAC>`, which blocked re-advertise),
      and `_run` needs `stdin=DEVNULL` (else `bluetoothctl` grabs the TTY)

### Phase H — Setup modal UI ⬜ (not started)
- [ ] Modal markup in `static/setup_modal.html`, loaded on demand
- [ ] Phases mirror the shell script: adapter check → ERTM → pairing-mode prompt → scan with progress bar → device picker → pair with phase progress bar → verify
- [ ] Consumes the SSE stream from `/api/gamepad/setup/events`
- [ ] Cancel button at any phase

---

## Open Items (resolved)

1. ~~Naming: arm / disarm replacement~~ → Both inputs use an explicit deadman (Shift for keyboard, L1 for gamepad).
2. ~~WASD vs gamepad precedence~~ → Shift wins, then L1, otherwise idle.
3. ~~Best e-stop unlatch UX~~ → Hold-to-confirm button (UI) + LS+RS chord (gamepad), both 1 s hold.
4. ~~Modal embedded or split~~ → Split: dashboard becomes static files + `server.py`.

# Browser Gamepad (Phase I) — Plan

Remote drive via a gamepad plugged into the **operator's PC**, using the
browser's native `navigator.getGamepads()` API. No Bluetooth required on the
Jetson side; the controller talks to the browser, which sends HTTP POSTs.

Companion feature to Phase H (BT pairing). Both result in "driving with a
gamepad" but are fundamentally different:

| | Robot BT gamepad (evdev) | PC gamepad (Browser API) |
|---|---|---|
| Controller paired to | **Jetson** | **operator's PC** |
| Path | controller → BT → evdev → server.py | controller → browser → HTTP POST |
| Setup | One-time BT pairing (the modal) | Plug in, browser auto-detects |
| Works without browser | Yes | No |
| Use case | Local operation | Remote operation over any network |

---

## UX Design — "ON ROBOT" vs "ON THIS PC"

The current Gamepad card conflates both sources under a single ambiguous
`+ Pair Controller` label. This redesign makes both sources permanently
visible with unambiguous labels:

```
┌─ Gamepad ────────────────────────────────────────────────────────┐
│                                                                   │
│  ON ROBOT (Bluetooth)                    [Pair to Robot →]        │
│  ─────────────────────────────────────────────────────────       │
│  Xbox Wireless Controller                                         │
│  [stick widget · steer bar · throttle bar]                        │
│                                                                   │
│  ON THIS PC                                                       │
│  ─────────────────────────────────────────────────────────       │
│  No controller detected — plug one in to use                      │
│  (when connected: name + L1 deadman hint)                         │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

- **"ON ROBOT"** — the evdev path. Button renamed `Pair to Robot` (not just
  "Pair"). Live-state widget stays in this section.
- **"ON THIS PC"** — Browser Gamepad API. No button; auto-detected. The row
  exists permanently so the user can always see both sources and their states.
- The topbar input pill gets a new `PC-GAMEPAD` state.

---

## Drive Priority

Explicit decision — person physically at the robot always has override authority:

```
WASD / Touchpad  (manual deadman, Shift or pointer-held)
      ↓ fallback
Robot BT gamepad  (L1 on the Jetson-paired controller)
      ↓ fallback
PC gamepad  (L1 on the browser-connected controller)
```

When Robot BT L1 is held it wins over PC gamepad, even if PC gamepad L1 is also
held. This means a local operator can always take back control from a remote one.

The drive merger already resolves manual vs BT; the change is inserting BT above
PC in the fallback chain.

---

## Browser Gamepad API — how it works

`navigator.getGamepads()` returns a snapshot of all connected gamepads; it is
polled (not event-driven), which fits perfectly in the existing 10 Hz drive loop.
No server change, no new endpoint, no install on the Jetson.

Axis mapping reuses the same normalised values as the evdev path:

```
axes[0]  (left stick X)  → steer   (−1 left … +1 right)
axes[1]  (left stick Y)  → throttle (note: browser Y is inverted — up = −1, so
                                      throttle = −axes[1])
buttons[6]  (L1 / LB)   → deadman
```

This follows the W3C Standard Gamepad mapping, which Chrome/Firefox apply for
most USB/BT controllers (Xbox, PlayStation, most generics). Non-standard-layout
controllers may need remapping — deferred.

---

## Files touched

| File | Change |
|---|---|
| `tools/dashboard/static/index.html` | Rename button `Pair to Robot`; add "ON THIS PC" row (name + hint) inside `#gamepad-card` |
| `tools/dashboard/static/app.js` | `_pcGp` state; poll `navigator.getGamepads()` in merger; extend priority chain; extend pill; `updateGamepadWidget` shows "ON ROBOT" section only |
| `tools/dashboard/static/style.css` | `.gp-source-label` separator style |
| `docs/software_architecture/index.html` | Tick the Feature 4 Browser Gamepad item |
| `README.md` | Update drive mode table (add PC gamepad row) |

No server, firmware, serial, or e-stop changes.

---

## Implementation Phases

**Phase I-1 — Gamepad card UX restructure**
- Rename button to `Pair to Robot`.
- Add `.gp-source-label` dividers: "ON ROBOT (Bluetooth)" / "ON THIS PC".
- Add `#gp-pc-disconnected` / `#gp-pc-connected` rows (static text for now).
- `#gp-disconnected` / `#gp-connected` stay under "ON ROBOT".

**Phase I-2 — Browser gamepad detection + status**
- In the 10 Hz poll: call `navigator.getGamepads()`, pick first connected
  gamepad (index 0 preference, else first non-null).
- Store in `_pcGp = { connected, name, steer, throttle, deadman }`.
- `updateGamepadWidget()` updates the "ON THIS PC" row (name or "No controller
  detected — plug one in to use").
- No drive sending yet.

**Phase I-3 — Merger integration + priority**
- Insert PC gamepad as the last fallback in the drive merger:
  ```
  if (manual)         → use manual
  else if (btGp.deadman) → use btGp    (Robot BT — wins over PC)
  else if (pcGp.deadman) → use pcGp    (PC gamepad — fallback)
  ```
- Extend `updateInputPill()` — new `PC-GAMEPAD` state.
- Update `latchEstop` comment (PC gamepad is also blocked while latched, same
  as BT — merger already handles this since `_estopLatched` returns early).

**Phase I-4 — Docs + polish**
- Update README drive mode table (add PC Gamepad row).
- Tick Feature 4 Browser Gamepad item in `docs/software_architecture/index.html`.
- Update topbar `#topstatus` hint (already dynamic via `setDriveMode`; add note
  that a PC gamepad is detected if available).

---

## Test Checklist (manual)

- [ ] "ON ROBOT" section shows paired controller name + live state widget.
- [ ] `Pair to Robot` button opens the BT modal (unchanged behaviour).
- [ ] "ON THIS PC" shows "No controller detected" with no gamepad plugged in.
- [ ] Plug in a USB gamepad → name appears in "ON THIS PC" row.
- [ ] PC gamepad L1 + sticks drives the robot (when no BT deadman held).
- [ ] Both L1s held → Robot BT wins (local override takes precedence).
- [ ] Release BT L1 → PC gamepad takes over (if still held).
- [ ] WASD / Touchpad deadman still wins over both gamepads.
- [ ] E-stop blocks all sources; HOLD-TO-RESET re-arms all.
- [ ] Input pill shows GAMEPAD-ROBOT / GAMEPAD-PC / KEYBOARD / TOUCHPAD / IDLE.
- [ ] Unplug PC gamepad mid-drive → `_pcGp.connected` clears; robot stops
      (merger sees no active source).

---

## Open Questions (decided)

1. **Priority**: Robot BT > PC gamepad. Local operator always overrides remote.
2. **Label**: "ON THIS PC" (not "Browser" or "Remote").
3. **Axis mapping**: W3C Standard Gamepad only for now. Non-standard remapping
   deferred.
4. **Multiple PC gamepads**: use first connected (index 0 or first non-null).
   Multi-controller support deferred.

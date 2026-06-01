# WASD ↔ Virtual Touchpad Drive Mode — Plan

A feature improvement to **Feature 4: Gamepad / Keyboard Control**.

Add a **toggle** on the dashboard that switches the browser's manual drive
input between two mutually-exclusive modes:

- **WASD** (current behaviour) — Shift is the deadman; W/A/S/D set throttle/steer.
- **Touchpad** — a virtual touchpad on the dashboard. Press-and-hold inside the
  pad is the deadman (no Shift needed); the pointer's position sets steer (X)
  and throttle (Y).

Only one mode's controls are visible at a time: when the touchpad is shown the
WASD keys are hidden, and vice-versa. The Bluetooth gamepad path (L1 deadman)
is unaffected and continues to work in either mode.

---

## Mental Model — deadman symmetry is preserved

The whole drive system already rests on **explicit deadman** inputs (see
`gamepad_dashboard_plan.md`): nothing moves unless a deadman is actively held.
This feature keeps that invariant — it only changes *what* the keyboard's
deadman looks like in touchpad mode:

| Mode      | Deadman                          | Steer source        | Throttle source      |
|-----------|----------------------------------|---------------------|----------------------|
| WASD      | **Shift** held                   | A / D               | W / S                |
| Touchpad  | **pointer held down on the pad** | pointer X on pad    | pointer Y on pad     |
| Gamepad   | **L1** held                      | left stick X        | left stick Y         |

Release the deadman (release Shift, lift the pointer, release L1) → one final
`0,0` frame is sent and the firmware watchdog brakes. At rest, no drive frames
flow regardless of mode. This is the safety property we must not break.

---

## Input Precedence

The active *manual* source depends on the selected mode; gamepad is the
fallback. Manual (the selected mode's deadman) wins over gamepad, mirroring the
existing "Shift wins, then L1" rule.

| Drive mode | Manual deadman active | L1 held | Drives the robot |
|------------|-----------------------|---------|------------------|
| WASD       | Shift + W/A/S/D        | —       | **Keyboard**     |
| WASD       | no                    | yes     | **Gamepad**      |
| Touchpad   | pointer on pad        | —       | **Touchpad**     |
| Touchpad   | no                    | yes     | **Gamepad**      |
| either     | no                    | no      | nothing (idle)   |

In touchpad mode the keyboard is **inert** (W/A/S/D do nothing) — consistent
with the WASD keys being hidden. Esc / Space e-stop stays active in all modes.

---

## UX / Layout

Inside the existing `#wasd-card` (centre column, `index.html`):

```
┌─ Drive ─────────────────────────────────────────────┐
│  [ ⌨ WASD ]  [ ⊹ Touchpad ]      ← segmented toggle  │
│                                                       │
│   ── mode == wasd ──            ── mode == touchpad ──│
│     W                              ┌───────────┐      │
│   A S D     [Steer gauge]          │     ·     │  [Steer gauge]
│             [Output gauge]         │  (pad +   │  [Output gauge]
│   ⇧+W/S hint                       │   dot)    │      │
│                                    └───────────┘      │
│                                    press & drag hint  │
│                                                       │
│                    [ ■ E-STOP ]                       │
└───────────────────────────────────────────────────────┘
```

- The **steer / output gauges** and the **E-STOP** button are shared and always
  visible — only the WASD grid (+ its Shift hint) and the touchpad (+ its hint)
  swap.
- The **touchpad** is a square (~160×160) styled like the existing gamepad
  stick widget (`#gp-stick`): centre cross-lines, a draggable dot showing the
  current command. Pressing engages; dragging moves the dot; the dot snaps back
  to centre on release.
- The toggle is a small segmented control (reuses `.btn` styling family).
- Selected mode persists in `localStorage` (default **WASD**, = today's
  behaviour, so nothing changes for existing users until they toggle).

---

## Touchpad → command mapping

With the pad centre as origin and half-width `R`:

```
steer    = clamp(dx / R, -1, +1) * _maxSteer      // right = +steer
throttle = clamp(-dy / R, -1, +1)                 // up    = +throttle (forward)
```

- A small **centre deadzone** (e.g. ±6 px) maps to exactly 0 so a still finger
  doesn't creep.
- The dot is clamped to the pad bounds for display.
- `setPointerCapture` on pointerdown so dragging *outside* the pad still tracks
  until release (and a release anywhere counts as lifting the deadman).

---

## Files touched (dashboard only — no firmware, no server change)

| File | Change |
|------|--------|
| `tools/dashboard/static/index.html` | Add the segmented toggle; wrap the WASD grid + Shift hint in a `#mode-wasd` block; add a `#mode-touchpad` block (pad SVG + dot + hint). Gauges/E-STOP stay shared. |
| `tools/dashboard/static/style.css` | Toggle (segmented) styles; `.touchpad`, `.touchpad-dot`, engaged state; `.mode-hidden` helper. |
| `tools/dashboard/static/app.js` | `_driveMode` + `_touch` state; `setDriveMode()` (show/hide + persist + force-release); pointer handlers; extend the drive merger; gate keyboard to WASD mode; extend `updateInputPill` (add TOUCHPAD); update `#topstatus` text per mode. |

The server, serial protocol, gamepad path, and e-stop logic are untouched —
`/drive` still receives `{steer_deg, speed_mps}` exactly as now.

---

## Implementation Phases

**Phase 1 — UI scaffolding (no drive yet)**
- Add toggle + `#mode-wasd` / `#mode-touchpad` blocks + CSS.
- `setDriveMode(mode)` toggles visibility and persists to `localStorage`.
- Verify switching hides/shows the right block; gauges + E-STOP remain.

**Phase 2 — Touchpad input → state + gauges (still not sending)**
- Pointer down/move/up/cancel on the pad → `_touch = {active, steer, throttle}`.
- Move the dot; drive `updateGauges()` from `_touch` for live preview.
- Pointer capture; release self-centres and clears `_touch.active`.

**Phase 3 — Merger integration + precedence**
- Extend the 10 Hz merger: pick manual source by mode (WASD→Shift, Touchpad→
  `_touch.active`), else gamepad; send via existing `sendDrive`.
- Gate W/A/S/D so they're inert in touchpad mode.
- Extend `updateInputPill` (TOUCHPAD state) and `#topstatus` hint per mode.

**Phase 4 — Edge cases & polish**
- E-stop latched blocks touchpad engage (like keyboard); pad visually disabled.
- Switching mode while engaged/driving forces a release + `0,0` frame.
- Ignore secondary (multi-touch) pointers.
- Confirm release-anywhere always yields a final `0,0` (no drift).

**Phase 5 — Docs**
- Update Feature 4 rows in `docs/software_architecture/index.html` (the single
  source of truth) and the README control-mapping section.
- (No ADR needed — this is a UI option within an accepted feature, not a new
  architectural decision.)

---

## Test Checklist (manual, on the dashboard)

Implemented in commit `5178e16`. Pending hardware verification on Jetson:

- [ ] Toggle switches blocks; WASD grid and touchpad never show together.
- [ ] WASD mode behaves exactly as before (Shift+W/S/A/D drives; release stops).
- [ ] Touchpad: press centre → 0,0; drag right → +steer; drag up → +throttle;
      release → stops (final 0,0).
- [ ] Drag pointer off the pad while held → still tracks; lifting anywhere stops.
- [ ] Gamepad (L1 + sticks) still drives in **both** modes.
- [ ] Esc / Space e-stop works in both modes; while latched neither manual mode
      can drive; HOLD-TO-RESET re-arms.
- [ ] Selected mode persists across a page reload.
- [ ] Works with both mouse and touch (phone/tablet).
- [ ] Input pill shows KEYBOARD / TOUCHPAD / GAMEPAD / IDLE correctly.

---

## Open Questions / Decisions (defaults chosen — adjust before Phase 1)

1. **Deadman model for the touchpad** — chosen: **press-and-hold** (release =
   stop), to preserve the deadman safety property. *(Alternative: tap-to-engage
   then move, with a separate release — rejected as less safe and inconsistent
   with Shift/L1.)*
2. **Mapping shape** — square clamp per-axis (independent steer/throttle).
   *(Alternative: radial clamp to a circle. Square is simpler and matches the
   stick widget; easy to change.)*
3. **Default mode** — **WASD**, so current users see no change until they
   toggle.
4. **Touchpad location** — inside `#wasd-card`, replacing the WASD grid in
   place (keeps the shared gauges + E-STOP adjacent). *(Alternative: overlay the
   camera as a true "drive-by-looking" pad — bigger change, deferred.)*

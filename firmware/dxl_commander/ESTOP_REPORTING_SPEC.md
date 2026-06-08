# Firmware spec — make `e_stop` actually report the watchdog

Status: PROPOSED (for you to implement in `dxl_commander.ino`)
Scope: STATE-frame `e_stop` byte only. No protocol size/layout change.

## Problem

The `e_stop` byte in the STATE frame is **always 0** — it can never tell the
Jetson that the firmware watchdog fired. Trace:

- `sendStateFrame()` is called **only** from `processCmdFrame()` (`dxl_commander.ino:366`).
- `processCmdFrame()` clears the flag first: `eStopActive = false;` (`:322`), *before* the frame is built.
- The watchdog sets `eStopActive = true` (`:230`, via `eStop()`) but only prints text — it never transmits a STATE frame (`:781`).

So every STATE frame the host receives carries `e_stop = 0`. The watchdog
*safety* works (torque is dropped on comms loss), but it is never *reported*.

This matters because `e_stop` is part of the black-mata STATE contract — the
dashboard reads `state.e_stop` (`tools/dashboard/server.py` → UI line ~1331) and
that branch is currently dead.

## Goal

After a watchdog fire, the **next** STATE frame(s) must report `e_stop = 1`
reliably enough for a polling dashboard to catch it (i.e. not a single-frame
pulse that the poll can miss). The host clears it explicitly when it has seen it.

## Design (recommended: sticky latch + host ack)

Add a latch separate from the instantaneous `eStopActive`:

```c
static bool watchdogLatched = false;   // set when watchdog fires; cleared by host ack
```

1. **On watchdog fire** — in `eStop()` (around `:230`), also set the latch:
   ```c
   eStopActive    = true;
   watchdogLatched = true;
   ```

2. **Report the latch** — in `sendStateFrame()` (`:292`), report the latch, not
   the instantaneous flag:
   ```c
   stateBuf[STATE_OFF_ESTOP] = watchdogLatched ? 1 : 0;
   ```
   The latch stays 1 across many frames, so the dashboard reliably sees it.

3. **Host clears it via the CMD payload** — the CMD frame already reserves a
   64-byte payload (`[39..102]`, currently zeros). Define one ack bit:
   - `payload[0] bit0 (0x01)` = "clear watchdog-fired latch".
   In `processCmdFrame()`, after CRC has validated the frame:
   ```c
   if (buf[CMD_OFF_PAYLOAD] & 0x01) watchdogLatched = false;
   ```
   (`CMD_OFF_PAYLOAD = 39`.) Re-arming (`eStopActive = false` + re-applying
   torque) stays exactly as today — the latch is purely informational.

This keeps `eStopActive` as the real-time torque-cut state and `watchdogLatched`
as the "it fired, please acknowledge" signal the host can trust.

### Host side (separate change, `serial_driver.py` / dashboard)
- `build_cmd_frame()` gains an optional `clear_estop=False` → sets `payload[0] |= 0x01`.
- Dashboard: when it observes `state.e_stop == 1`, show the watchdog indicator,
  then send one CMD frame with `clear_estop=True` to acknowledge (or expose a
  "clear" button). Until acked, the indicator stays latched.

## Minimal alternative (if you don't want an ack path yet)

Report the **pre-clear** state in the reply that re-arms:

```c
bool wasEStopped = eStopActive;        // capture BEFORE clearing
eStopActive = false;
...
sendStateFrame(seq, wasEStopped);      // e_stop byte = wasEStopped
```

This emits a **one-frame** `e_stop = 1` pulse right after a comms gap. Simpler,
but a polling dashboard can miss the single frame — acceptable only if the host
reads every frame. The sticky-latch design above is preferred.

## Test hook

Once implemented, `firmware_estop_test.py` Phase 2 can additionally assert
`state.e_stop == 1` on the first frame after the silence window (instead of
relying solely on the by-hand "is it limp?" observation), and the SEC 2
checklist item *"e_stop: 0 on power-up — not stuck in e-stop"* becomes a real
check rather than a trivially-true one.
```

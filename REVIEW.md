# Code Review — Improvement Todo List

Generated: 2026-04-06

---

## Priority 1 — Critical (Security / Stability)

- [ ] **Token parsing buffer overflow**
  - Files: `dxl_imu_commander.ino:211–224`, `dxl_commander.ino:146–159`
  - `pos` is `uint8_t`, overflows silently at 256; token truncation is silent
  - Fix: Change `pos` to `uint16_t`; return error on truncation

- [ ] **Integer conversion without validation**
  - File: `dxl_imu_commander.ino:226–236`
  - `toInt()` returns `0` on parse error, indistinguishable from valid value `0`; no range checking
  - Fix: Add min/max bounds parameter to `tokenInt()`

- [ ] **`micros()` wraparound in U2D2 bridge**
  - File: `dxl_u2d2_bridge.ino:72–76`
  - `micros()` wraps every ~70 minutes; timeout logic breaks after that
  - Fix: Use `(uint32_t)(now - start) > timeout` pattern

- [ ] **No watchdog timer**
  - File: `setup()` in all sketches
  - A hang or deadlock requires manual power cycle
  - Fix: Initialize STM32F103RE IWDG in `setup()`, pet it in `loop()`

---

## Priority 2 — High (Correctness / Robustness)

- [ ] **`MONITOR` command blocks indefinitely**
  - File: `dxl_imu_commander.ino:337–372`
  - `while(true)` holds the processor; serial buffer overflow may trigger hard reset
  - Fix: Add 30s timeout; yield between cycles

- [ ] **IMU `enable*()` calls not verified**
  - File: `dxl_imu_commander.ino:123–138`
  - Each `imu.enable*()` can fail silently; `imuReady` is set `true` regardless
  - Fix: AND all return values; set `imuReady = false` if any fails

- [ ] **No retry on servo read failure**
  - File: `dxl_imu_commander.ino:350–354`
  - A single bus glitch immediately aborts the MONITOR session
  - Fix: Retry 3x with 50ms backoff before giving up

- [ ] **NUDGE does not restore torque**
  - File: `dxl_servo_nudge.ino:66`
  - After mode restoration, `dxl.torqueOn()` is never called
  - Fix: Add `dxl.torqueOn(SERVO_ID)` after restoring mode limits

---

## Priority 3 — Medium (Maintainability)

- [ ] **~150 lines of duplicated code across 4 sketches**
  - `getMode()`, `waitForMotion()`, conversion macros, and token parsing are copy-pasted
  - Fix: Create `firmware/shared/` with `ax12a_constants.h`, `ax12a_helpers.h`, `command_parser.h`

- [ ] **`Arduino String` class used in parsing hot path**
  - File: `dxl_imu_commander.ino:211–236`
  - Heap allocations per token; potential fragmentation over long uptime
  - Fix: Replace with stack-allocated `char[]` buffers

- [ ] **Hard-coded delays with no rationale**
  - File: `dxl_imu_commander.ino:200, 397, 419, 432`
  - Fix: Define named constants (`DXL_MODE_SWITCH_DELAY_MS`, etc.) at the top of the file

- [ ] **No protocol version on startup**
  - Host cannot verify firmware compatibility
  - Fix: Print `# PROTOCOL_VERSION,1.0` in `setup()`

---

## Priority 4 — Low (Polish)

- [ ] **SPI scanner diagnostic output unclear**
  - File: `spi_scanner.ino:102–109`
  - Explain what `0xFF` and `0x00` responses mean in comments

- [ ] **U2D2 bridge lacks framing documentation**
  - File: `dxl_u2d2_bridge.ino:1–30`
  - Add comment block explaining timeout-based packet framing assumptions

- [ ] **NUDGE clips position silently**
  - File: `dxl_imu_commander.ino` (nudge handler)
  - `constrain()` clips without notifying the user
  - Fix: Log a warning if the requested position was clipped

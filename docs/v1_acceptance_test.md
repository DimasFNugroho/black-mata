# Black-Mata v1 — Field Acceptance Test Procedure

End-to-end system verification on the real robot. Run top-to-bottom on a
**clean reboot** with no pre-warmed processes. Sign off every section before
proceeding to the next.

**Test rig:**
- Black-Mata robot (powered, 12 V rail live)
- Jetson Nano with RTL8761B dongle plugged in
- USB camera mounted, cable connected
- OpenCM9.04 connected via USB Serial
- x86 laptop (operator machine) on the same LAN
- Machenike G3 V2 gamepad (and optionally a USB gamepad for the PC-side test)

---

## SECTION 0 — Pre-flight Hardware

- [ ] 12 V battery voltage reads ≥ 11.0 V with a multimeter before powering on
- [ ] All 8 servo connectors seated; daisy-chain continuity (no open links)
- [ ] USB Camera plugged in; `lsusb` on Jetson shows the camera vendor/product
- [ ] RTL8761B dongle plugged in; `lsusb` shows `0bda:a760`
- [ ] OpenCM9.04 USB Serial connected; `ls /dev/ttyACM*` shows at least one device
- [ ] `/dev/opencm` symlink exists (udev rule installed): `ls -l /dev/opencm`
- [ ] `/dev/robot_camera` symlink exists: `ls -l /dev/robot_camera`

---

## SECTION 1 — Cold Boot Baseline

- [ ] `sudo reboot` — full power cycle, do not SSH in until the Jetson is up
- [ ] After boot, `hciconfig` lists `hci0` (BT adapter auto-powered)
- [ ] `rfkill list bluetooth` shows **not** blocked
- [ ] `systemctl is-active bluetooth` → `active`

### 1a — Bluetooth Host Commissioning Gate

```bash
bash tools/gamepad/check_bluetooth_host.sh
```

- [ ] All 9 checks **PASS** (or only the ERTM runtime as WARN — acceptable)
- [ ] Exit code is `0`
- [ ] Adapter is **Powered: yes** without any manual intervention

> **If any FAIL here: run `bash tools/gamepad/commission_bluetooth.sh` and follow
> its prompts before continuing. A clean cold-boot PASS is the gate.**

---

## SECTION 2 — Firmware Verification

### 2a — Build and flash

```bash
# On x86 laptop:
python build.py   # select black_mata, Release
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh
```

- [ ] Build completes without errors
- [ ] Flash script connects over SSH, uploads binary, resets the board
- [ ] OpenCM9.04 LED blinks post-flash (firmware running)

### 2b — Serial frames

```bash
# On x86:
./tools/monitor/serial_monitor.sh
```

- [ ] STATE frames arriving at ~10 Hz (observe sequence numbers incrementing)
- [ ] No CRC errors in the first 30 s
- [ ] `e_stop: 0` in the state output (not stuck in e-stop on power-up)

### 2c — Servo discovery

```bash
# On Jetson (ackermann_ui stopped):
python3 tools/dynamixel/dxl_identify.py
```

- [ ] All **8 servo IDs** discovered (4 steer + 4 drive)
- [ ] Each servo responds with its model (AX-12A)
- [ ] No ID conflicts

---

## SECTION 3 — Ackermann UI Calibration Check

> Run ackermann_ui **only** — dashboard must be stopped.

```bash
python3 tools/ackermann_ui/server.py --port /dev/opencm
# open http://<jetson-ip>:8081
```

### 3a — Servo ID map

- [ ] Servo ID map matches physical wheel roles (FL_s, FR_s, RL_s, RR_s, FL_d, FR_d, RL_d, RR_d)
- [ ] Nudge each steer servo individually — corresponding wheel moves
- [ ] Nudge each drive servo individually — corresponding wheel spins

### 3b — Steer centre calibration

- [ ] Click **Torque off steering** — all four steer servos go limp
- [ ] Physically push all four wheels to straight-ahead (visually verify)
- [ ] Click **Record neutral** — offsets fill in automatically
- [ ] Click **Send to robot** — wheels hold straight under torque
- [ ] Steer angle readback on bird's-eye ≈ 0.0° for all four wheels at neutral

### 3c — Steer range sweep

- [ ] Drag steer slider to +30° — all four wheels steer correctly (Ackermann geometry, inner wheel tighter than outer)
- [ ] Drag to −30° — correct opposite geometry
- [ ] Return to 0° — wheels return to straight, no drift

### 3d — Config save/load round-trip

- [ ] Click **Save config** — terminal confirms write to `ackermann_config.json`
- [ ] Close and reopen the browser tab — click **Load config** — values repopulate correctly
- [ ] Inspect `tools/ackermann_ui/ackermann_config.json` — all expected fields present including `default_drive_mode`

### 3e — Battery thresholds display

- [ ] Set battery thresholds to known values, save, reload ackermann_ui — values persist

### 3f — Default drive mode setting

- [ ] Toggle to **⊹ Touchpad** — "Saved" flash appears
- [ ] Check `ackermann_config.json` — `"default_drive_mode": "touchpad"`
- [ ] Toggle back to **⌨ WASD** — "Saved" flash appears
- [ ] Check `ackermann_config.json` — `"default_drive_mode": "wasd"`

> **Stop ackermann_ui before continuing.**

---

## SECTION 4 — Camera Stream

```bash
# On Jetson:
python3 tools/camera/camera_test.py
```

- [ ] Terminal shows "Camera server started on :8083"
- [ ] `curl -I http://<jetson-ip>:8083/stream` returns `200` with `multipart/x-mixed-replace`
- [ ] `curl http://<jetson-ip>:8083/health` returns `{"status":"ok"}`
- [ ] Open `http://<jetson-ip>:8083/stream` in a browser — live MJPEG visible

### 4a — Camera auto-reconnect

- [ ] With stream open in browser, **physically unplug the USB camera**
- [ ] `/health` returns `503` within ~2 s
- [ ] Stream goes blank in the browser (camera_test.py stays running — no crash)
- [ ] **Replug the camera** — `/health` returns `200` within ~4 s
- [ ] Stream resumes in the browser **without any page refresh**

> Leave camera_test.py running for the rest of the test.

---

## SECTION 5 — Dashboard — Startup and Config

```bash
# On Jetson (camera_test.py already running):
python3 tools/dashboard/server.py --port /dev/opencm
# open http://<jetson-ip>:8082 on the operator laptop
```

- [ ] Dashboard loads without JS console errors (open browser DevTools → Console)
- [ ] `conn-badge` shows **connected** (green)
- [ ] `cfg-info` bar shows correct max steer and max output % from `ackermann_config.json`
- [ ] Config refreshes within 5 s if you manually edit and save the JSON
- [ ] Drive mode is **WASD** on first load (matches `default_drive_mode` in config)

### 5a — Camera feed in dashboard

- [ ] MJPEG feed visible in the camera panel (proxied via :8082)
- [ ] Frame is rotated correctly (robot-mounted orientation)
- [ ] No CORS errors in console

### 5b — Bird's-eye live update

- [ ] Bird's-eye SVG shows all four wheel icons
- [ ] With robot at rest (no drive command): all angles read ≈ 0.0°
- [ ] Manually push a front wheel gently — angle label updates in real time
- [ ] Ackermann arc appears when steer angle > 0.5° and disappears at 0

### 5c — Temperature heatmap

- [ ] All eight servos show temperature labels (S:°C, D:°C)
- [ ] Colours start cool-blue at ambient (~25–35°C)
- [ ] After 5 min of drive testing, verify colours shift warmer on active servos

### 5d — Battery gauge

- [ ] Battery fill bar shows non-zero percentage
- [ ] Voltage displayed matches multimeter reading ± 0.2 V
- [ ] Colour is green (> 11.0 V), yellow if low, red if critical

---

## SECTION 6 — WASD Keyboard Drive

> All drive tests: **raise the robot off the ground** or run on a clear area.

- [ ] Topbar shows **"Hold ⇧ + WASD to drive or L1 + sticks (gamepad)."**
- [ ] Drive mode toggle shows **⌨ WASD** selected (active)
- [ ] W/A/S/D keys pressed without Shift → **no movement** (deadman not held)

### 6a — Forward / reverse

- [ ] Hold **Shift + W** → robot moves forward; release Shift → robot stops
- [ ] Hold **Shift + S** → robot moves in reverse; release → stops
- [ ] Output gauge bar fills correctly during drive; returns to 0 on release

### 6b — Steering

- [ ] Hold **Shift + A** → robot steers left; bird's-eye arcs correct Ackermann geometry
- [ ] Hold **Shift + D** → steers right
- [ ] Combined **Shift + W + A** → forward-left arc; gauge bars update simultaneously

### 6c — Input pill

- [ ] While Shift held: pill shows **KEYBOARD** (blue)
- [ ] Release Shift: pill shows **IDLE** (or GAMEPAD: NONE if no BT gamepad)

### 6d — Firmware watchdog (kill network)

- [ ] Hold Shift + W (robot driving forward)
- [ ] On the Jetson: `sudo iptables -I INPUT -p tcp --dport 8082 -j DROP` (simulate network loss)
- [ ] Robot must stop within **≤ 300 ms** (keepalive timeout)
- [ ] Restore: `sudo iptables -D INPUT -p tcp --dport 8082 -j DROP`
- [ ] Dashboard reconnects; robot controllable again

---

## SECTION 7 — Touchpad Drive Mode

- [ ] Click **⊹ Touchpad** toggle — WASD grid disappears, touchpad SVG appears
- [ ] Hints update: "drag up → fwd / drag right → steer / Spc/Esc → e-stop"
- [ ] Topbar updates: "Press & drag the touchpad to drive..."
- [ ] W/A/S/D keyboard keys are **completely inert** in Touchpad mode

### 7a — Basic touchpad control

- [ ] Click and hold centre of pad → robot does not move (centre deadzone)
- [ ] Click hold + drag right → robot steers right; steer gauge fills; bird's-eye updates
- [ ] Click hold + drag up → robot drives forward; output gauge fills
- [ ] Release pointer → robot stops; dot snaps back to centre

### 7b — Off-pad drag tracking

- [ ] Press inside the pad, drag pointer **outside** the pad boundary — input still tracks (pointer capture)
- [ ] Release pointer anywhere off-pad → robot stops cleanly

### 7c — Mode persistence via config

- [ ] While in Touchpad mode, go to ackermann_ui → toggle default to **⊹ Touchpad** → Save
- [ ] Reload the dashboard — opens in **Touchpad** mode (config-driven, not localStorage)
- [ ] Go back to ackermann_ui → toggle to **⌨ WASD** → Save → reload dashboard → WASD mode

### 7d — Mid-session toggle safety

- [ ] Drive in Touchpad mode (pointer held)
- [ ] While robot is moving, click **⌨ WASD** toggle
- [ ] Robot stops immediately (mode switch force-releases touchpad)

---

## SECTION 8 — E-Stop (all six paths)

> The e-stop must be **instantaneous** on trigger and require a deliberate
> hold to release. Test each path independently; re-arm between each.

### 8a — E-stop via Space / Escape key

- [ ] Robot driving (Shift + W) → press **Space** → instant stop; button turns amber "↻ HOLD TO RESET"
- [ ] Shift + W while latched → **no movement**
- [ ] Hold button for 1 s → latch clears; button returns to red "■ E-STOP"

### 8b — E-stop via E-STOP button click

- [ ] Robot driving → click red **■ E-STOP** button → instant stop
- [ ] Hold button 1 s → re-arm

### 8c — E-stop via gamepad combo (L1 + D-pad diagonal)

*(requires BT gamepad paired — come back here after Section 9 if needed)*

- [ ] Robot driving via L1 + sticks → press **L1 + any D-pad diagonal** → instant stop, rising-edge triggered (not held)
- [ ] Confirm: single press latches; button turns amber on dashboard

### 8d — E-stop re-arm via LS + RS hold

- [ ] While latched: hold **both stick clicks (LS + RS)** for 1 s → latch clears
- [ ] Partial hold (< 1 s) → latch stays; must complete full hold

### 8e — E-stop via firmware watchdog (serial kill)

- [ ] Robot driving → **physically unplug the OpenCM USB cable** from Jetson
- [ ] All servos must stop within **≤ 500 ms** (firmware watchdog fires on its own power rail)
- [ ] Dashboard shows `no robot (sim)` badge
- [ ] Replug OpenCM — dashboard reconnects; `send_estop()` fires on reconnect

### 8f — E-stop via camera loss (NOT expected to trigger e-stop)

- [ ] Unplug camera while driving
- [ ] **Robot must keep driving** — camera loss must not interrupt drive path (verify isolation)
- [ ] Camera feed blanks; drive continues normally

---

## SECTION 9 — Bluetooth Gamepad (Robot BT path)

### 9a — Terminal pairing (baseline)

```bash
bash tools/gamepad/reset_bluetooth_host.sh    # clear existing bonds
bash tools/gamepad/setup_gamepad.sh --verbose
```

- [ ] Scan finds the G3 V2 by name
- [ ] Pairing completes: "Pairing successful"
- [ ] Trust and connect succeed
- [ ] `hcitool con` shows an active ACL connection

### 9b — Dashboard modal pairing (end-user path)

```bash
bash tools/gamepad/reset_bluetooth_host.sh    # clear bond from 9a
```

- [ ] In dashboard: click **Pair to Robot** button — modal opens
- [ ] "Continue" prompts to put controller in pairing mode
- [ ] Click Continue → progress bar advances through scan phase
- [ ] G3 V2 appears in device list
- [ ] Click controller name → pair/trust/connect progress shown
- [ ] Verify phase shows controller input live
- [ ] "ON ROBOT (Bluetooth)" row updates with controller name
- [ ] Modal closes or shows success; no terminal needed at any step

### 9c — Gamepad drive

- [ ] L1 released → **no movement** (deadman not held)
- [ ] L1 held + left stick forward → robot drives forward
- [ ] L1 held + left stick back → reverse
- [ ] L1 held + left stick left/right → steer; bird's-eye arcs update
- [ ] Release L1 mid-drive → robot stops immediately
- [ ] Input pill shows **GAMEPAD: Machenike G3 V2** (or similar name) while deadman held

### 9d — Gamepad range test

- [ ] Pair the controller from **1.5 m away** (confirm the RSSI fix works — previously a known failure point)
- [ ] Pairing completes successfully at range

### 9e — Gamepad → e-stop → re-arm full loop

- [ ] L1 + sticks driving → L1 + D-pad diagonal → instant e-stop (rising edge)
- [ ] Try to drive (L1 + sticks) while latched → **no movement**
- [ ] LS + RS hold 1 s → re-arms
- [ ] Drive resumes normally

### 9f — Controller swap mid-session

- [ ] While controller is connected (shows in dashboard), click **Pair to Robot** again
- [ ] Modal opens without crashing the server
- [ ] Pair the same (or a different) controller
- [ ] New controller name appears in "ON ROBOT" row

---

## SECTION 10 — PC Gamepad (Browser Gamepad API path)

*(Use a USB gamepad or a BT gamepad paired to the operator laptop via OS settings)*

- [ ] "ON THIS PC" row shows **"No controller detected — plug in via USB or pair via your OS Bluetooth settings."**

### 10a — USB gamepad detection

- [ ] Plug USB gamepad into operator laptop
- [ ] "ON THIS PC" row shows **controller name within 100 ms** (next 10 Hz poll)
- [ ] No page reload needed

### 10b — PC gamepad drive

- [ ] No BT gamepad L1 held → hold **L1/LB on PC gamepad** + sticks → robot drives
- [ ] Input pill shows **GAMEPAD-PC**
- [ ] Release L1 → robot stops

### 10c — Priority: Robot BT wins

- [ ] Both gamepads connected; hold **both L1s simultaneously**
- [ ] Robot must follow **Robot BT gamepad** (local override)
- [ ] Release Robot BT L1 only → robot **immediately** switches to PC gamepad input (if PC L1 still held)
- [ ] Release PC L1 → robot stops

### 10d — PC gamepad mid-drive unplug

- [ ] Driving via PC gamepad → **unplug the USB gamepad** mid-drive
- [ ] Robot stops within one 100 ms merger tick
- [ ] "ON THIS PC" row returns to "No controller detected"

---

## SECTION 11 — Input Priority Full Matrix

Run each row; confirm only the expected source drives the robot:

| Shift | Touchpad pressed | Robot BT L1 | PC L1 | Expected source |
|---|---|---|---|---|
| ✅ | — | — | — | KEYBOARD |
| — | ✅ | — | — | TOUCHPAD |
| — | — | ✅ | — | GAMEPAD-ROBOT |
| — | — | — | ✅ | GAMEPAD-PC |
| ✅ | — | ✅ | — | KEYBOARD (Shift wins) |
| — | ✅ | ✅ | — | TOUCHPAD (manual wins) |
| — | — | ✅ | ✅ | GAMEPAD-ROBOT (local wins) |
| — | — | — | — | IDLE (nothing moves) |

- [ ] All 8 rows confirmed correct

---

## SECTION 12 — Remote Access via ngrok

```bash
# On Jetson (dashboard and camera already running):
ngrok http 8082
```

- [ ] ngrok prints a `https://*.ngrok-free.app` URL
- [ ] Open that URL on a **mobile phone on a different network** (4G/5G)
- [ ] Dashboard loads fully (camera feed, bird's-eye, all widgets)
- [ ] Drive the robot from the phone using the **Touchpad** — full round trip over internet
- [ ] E-stop from the phone works
- [ ] Stop ngrok (`Ctrl+C`) — robot is no longer reachable from external URL
- [ ] Dashboard on LAN still works normally (ngrok is not a dependency)

---

## SECTION 13 — Systemd Auto-start

```bash
bash tools/setup/autostart.sh enable   # enable camera + dashboard services
sudo reboot
```

- [ ] After reboot, camera and dashboard start **without any manual SSH**
- [ ] Dashboard reachable at `:8082` within 30 s of the Jetson becoming pingable
- [ ] Camera feed live in the dashboard (camera_test service up before dashboard)
- [ ] Verify with `systemctl status black-mata-camera black-mata-dashboard`
- [ ] Both show `active (running)`, no failed restarts

### 13a — Disable and re-enable

```bash
bash tools/setup/autostart.sh disable
sudo reboot
```

- [ ] After reboot, neither service starts automatically
- [ ] No stale processes on port 8082 or 8083

```bash
bash tools/setup/autostart.sh enable
```

- [ ] Services re-enabled; no reboot needed to start them manually

---

## SECTION 14 — Full Drive Session (5-minute endurance)

Drive the robot in a real environment for **5 continuous minutes**, cycling through all input modes.

- [ ] **0:00–1:30** — WASD keyboard: figure-of-eight path; verify Ackermann arcs on bird's-eye track the geometry
- [ ] **1:30–3:00** — Touchpad: same path; verify responsiveness; drag off-pad mid-drive intentionally — no runaway
- [ ] **3:00–4:00** — Robot BT gamepad: full throttle sprint and hard steer; verify no frame drops or CRC errors in serial monitor
- [ ] **4:00–5:00** — PC gamepad: drive from the laptop; verify latency is acceptable for teleoperation
- [ ] Battery gauge has decreased (voltage drop under load is visible and correctly displayed)
- [ ] Temperature labels on the drive servos have shifted colour (thermal load visible)
- [ ] No servo disconnection events (all 8 IDs present throughout)
- [ ] No dashboard JS errors the entire session (console clean)

---

## SECTION 15 — Fault Injection

### 15a — Jetson dashboard crash

- [ ] Robot idle (no deadman held)
- [ ] Kill the dashboard process: `pkill -f server.py`
- [ ] Confirm robot stays stopped (firmware watchdog — no spurious motion on server crash)
- [ ] Restart dashboard — `conn-badge` returns to green; all widgets repopulate

### 15b — Serial cable hot-unplug mid-drive

- [ ] Robot driving via Shift + W
- [ ] Unplug OpenCM USB cable
- [ ] Servos stop within **≤ 500 ms** (firmware watchdog on its own rail)
- [ ] Replug — server reconnects; `send_estop()` fires before allowing drive

### 15c — Camera cable hot-unplug mid-drive

- [ ] Robot driving
- [ ] Unplug camera
- [ ] Robot **continues driving** uninterrupted — drive and camera are isolated processes
- [ ] Camera feed blanks; replug → feed restores without any action
- [ ] Drive still works throughout

### 15d — Browser tab close mid-drive

- [ ] Robot driving via Shift + W
- [ ] Close the browser tab
- [ ] Robot stops within **≤ 300 ms** (keepalive sees no POST; server sends zero-speed frame)

### 15e — Bluetooth gamepad out-of-range

- [ ] Driving via Robot BT gamepad (L1 held)
- [ ] Carry the gamepad out of BT range (5+ metres, behind a wall)
- [ ] Confirm BT link drops; `_gp.connected` clears; robot stops
- [ ] Bring controller back in range — auto-reconnect (GamepadReader thread) restores within a few seconds

---

## SECTION 16 — Commissioning Tools Verification

### 16a — check_bluetooth_host.sh (already done in Section 1 — confirm result still clean)

```bash
bash tools/gamepad/check_bluetooth_host.sh
```

- [ ] 8 PASS / 1 WARN (ERTM runtime — acceptable) / 0 FAIL

### 16b — commission_bluetooth.sh

```bash
bash tools/gamepad/commission_bluetooth.sh
```

- [ ] Reports **"STATE: fully commissioned and ready."** immediately (no steps needed post-setup)
- [ ] Embedded check_bluetooth_host output within the guide: same PASS/WARN counts

### 16c — reset + re-commission cycle (one time, to prove reproducibility)

```bash
bash tools/gamepad/reset_bluetooth_host.sh
sudo reboot
bash tools/gamepad/commission_bluetooth.sh   # Tier 2 prompt
bash tools/gamepad/setup_bluetooth_host.sh
sudo reboot
bash tools/gamepad/check_bluetooth_host.sh   # must pass on cold boot
```

- [ ] After reset: check shows 4+ FAIL (as expected — clean slate)
- [ ] After re-commission + reboot: check returns to 8 PASS / 0 FAIL
- [ ] Proves the procedure is **repeatable on this unit**

### 16d — push_gamepad_to_jetson.sh (from x86)

```bash
bash tools/gamepad/push_gamepad_to_jetson.sh
```

- [ ] Password prompted once (SSH multiplexing)
- [ ] All 14 files transferred successfully
- [ ] No "Is a directory" or permission errors

---

## SECTION 17 — Final State Verification

After all tests, on a clean reboot with auto-start enabled:

- [ ] Dashboard reachable at `:8082` without any SSH
- [ ] Camera feed live without any SSH
- [ ] `check_bluetooth_host.sh` still passes (commissioning survived all the testing)
- [ ] `ackermann_config.json` still has correct calibration values (steer offsets intact)
- [ ] `default_drive_mode` in config is `"wasd"` (set it back to the shipping default)
- [ ] No stale bluetooth bonds from test pairings (run `bt_setup.py` → `remove '*'` confirms or pair fresh)

---

## SIGN-OFF

| Section | Result | Notes |
|---|---|---|
| 0 — Pre-flight hardware | PASS / FAIL | |
| 1 — Cold boot + BT commissioning | PASS / FAIL | |
| 2 — Firmware + serial | PASS / FAIL | |
| 3 — Ackermann UI calibration | PASS / FAIL | |
| 4 — Camera + auto-reconnect | PASS / FAIL | |
| 5 — Dashboard startup + config | PASS / FAIL | |
| 6 — WASD keyboard drive | PASS / FAIL | |
| 7 — Touchpad drive mode | PASS / FAIL | |
| 8 — E-stop (all 6 paths) | PASS / FAIL | |
| 9 — Robot BT gamepad | PASS / FAIL | |
| 10 — PC gamepad | PASS / FAIL | |
| 11 — Input priority matrix | PASS / FAIL | |
| 12 — ngrok remote access | PASS / FAIL | |
| 13 — Systemd auto-start | PASS / FAIL | |
| 14 — 5-min drive endurance | PASS / FAIL | |
| 15 — Fault injection | PASS / FAIL | |
| 16 — Commissioning tools | PASS / FAIL | |
| 17 — Final state | PASS / FAIL | |

**Tested by:** ___________________________  
**Date:** ___________________________  
**Unit serial / hostname:** ___________________________  
**Firmware commit:** `git rev-parse --short HEAD`  
**Result:** ☐ APPROVED FOR RELEASE &nbsp;&nbsp; ☐ BLOCKED — see notes

---

> *Defects found during this test should be filed as issues before the release
> tag is cut. The release tag should only be created after a complete PASS on
> a cold-boot run with zero open blockers.*

# Black-Mata

x86 build toolchain and Jetson runtime tools for the Black-Mata robot.
Firmware is compiled on x86, flashed to the OpenCM9.04 over SSH, and the Jetson runs Python scripts to communicate with the board.

---

## V1 Feature Status

### Firmware — OpenCM9.04

- [x] Binary CMD frame receiver (105 bytes, CRC-16 CCITT)
- [x] Binary STATE frame transmitter (202 bytes, CRC-16 CCITT)
- [x] Dual-mode serial (text commands + binary frames on same port)
- [x] Dynamixel JOINT / WHEEL mode switching per servo
- [x] Firmware watchdog — zeros all drive speeds if no frame received within 500 ms
- [x] Round-robin temperature & voltage polling (one servo per frame)
- [x] Correct AX-12A Present Speed readback via direct address 38 (not PRESENT_VELOCITY)
- [x] `cmdMonitor` shows output % for WHEEL mode servos, rpm for JOINT mode *(needs reflash)*

### Robot Agent — Jetson (`software/robot/`)

- [x] Serial driver — binary frame encode/decode, background recv thread, CRC validation (`serial_driver.py`)
- [x] Ackermann kinematics — 4WS counter-phase, per-wheel speed differential, output fraction API (`ackermann.py`)
- [x] E-stop handler — WebSocket silence detection (500 ms), zero-speed frame dispatch (`estop.py`)
- [x] Camera — V4L2 capture, MJPEG encode, background thread, auto-reconnect on hardware disconnect (`camera.py`)

### Operator Dashboard (`tools/dashboard/`)

- [x] Single-page HTML dashboard (Vanilla JS, no build step)
- [x] Keyboard WASD drive control
- [x] Live MJPEG camera feed — proxied from `camera_test.py`
- [x] Camera auto-reconnect — dashboard detects hardware and software disconnections without page refresh
- [x] E-stop button
- [x] Live config display — reads `ackermann_config.json`, refreshes every 5 s
- [x] Bird's-eye view — live per-wheel steer angles and drive direction arrows
- [x] Per-wheel temperature heatmap — colour-coded overlaid on bird's-eye view
- [x] Battery gauge — live voltage with colour-coded fill bar
- [ ] Gamepad → dashboard drive integration (evdev path, Jetson-local)
- [ ] Browser Gamepad API → remote drive via dashboard (operator-side gamepad over network)
- [ ] Servo status panel (per-servo voltages, temperatures, positions, modes)

### Bluetooth Gamepad (`tools/gamepad/`)

- [x] BT pairing automation — interactive scan/pair/trust/connect via persistent bluetoothctl session (`setup_gamepad.sh`)
- [x] ESP32-C6 as BLE HCI adapter — registers as hci0 via hciattach; systemd service auto-starts at boot (`setup_esp32_hci.sh`)
- [x] ESP32-C6 HCI firmware — BLE controller-only mode over UART0 at 115200 baud, built with ESP-IDF v5 (`firmware/esp32_hci/`)
- [x] Gamepad input validator — live display of all axes (raw + normalised) and buttons; drive preview with steer, throttle, e-stop combo, arm (`gamepad_test.py`)
- [x] Per-controller axis calibration — G3 V2 hardware calibration procedure + software range sweep; saves to JSON (`--calibrate`)
- [ ] Gamepad → dashboard drive integration (evdev path, Jetson-local)
- [ ] Browser Gamepad API → remote drive via dashboard (operator-side gamepad over network)

### Infrastructure

- [x] Remote firmware flash toolchain — compile x86, flash to OpenCM9.04 over SSH (`tools/remote_update/`)
- [x] Serial monitor — stream OpenCM debug output from Jetson to x86 (`tools/monitor/`)
- [x] ngrok tunnel — share dashboard publicly without exposing the OS (`tools/setup/ngrok_setup.sh`)
- [x] Systemd auto-start — enable/disable camera, dashboard, and ngrok services with one command (`tools/setup/autostart.sh`)

### Calibration & Commissioning Tools (not in v1 architecture, added during development)

- [x] Ackermann UI — browser-based parameter tuning, bird's-eye visualisation, servo state (`tools/ackermann_ui/`)
- [x] Servo ID identification — nudge each servo and map to wheel role (`tools/dynamixel/dxl_identify.py`)
- [x] Steer centre calibration — torque-off, physically align, record neutral offsets
- [x] Trapezoidal steering profile — smooth motion on large angle commands, drag vs. click detection
- [x] Camera test server — standalone MJPEG stream over stdlib HTTP, no serial port needed (`tools/camera/camera_test.py`)
- [x] Camera udev setup — installs stable `/dev/robot_camera` symlink tied to USB vendor/product ID (`tools/camera/setup_udev.py`)

---

## Setting up Bluetooth Gamepad (G3 V2 + ESP32-C6)

The Machenike G3 V2 connects to the Jetson over Bluetooth. An ESP32-C6 is used as a temporary BLE adapter until a USB dongle is available.

**Step 1 — Flash ESP32-C6 (x86, once)**
```bash
. $HOME/esp/esp-idf/export.sh      # source ESP-IDF v5
bash tools/gamepad/flash_esp32.sh  # build & flash via CH343 port
```

**Step 2 — Register ESP32 as Bluetooth adapter (Jetson, once)**
Plug the ESP32-C6 CH343 port into the Jetson, then:
```bash
bash tools/gamepad/setup_esp32_hci.sh
```
This attaches the ESP32 as `hci0` and installs a systemd service so it registers automatically at every boot.

**Step 3 — Hardware-calibrate the G3 V2 (once)**

1. Press **Home + Select + B** simultaneously — LEDs blink blue.
2. Move all sticks and triggers to their full extents.
3. Press **Start** to confirm.

**Step 4 — Pair the controller (Jetson)**
```bash
bash tools/gamepad/setup_gamepad.sh
```

**Step 5 — Software calibration (Jetson)**
```bash
python3 tools/gamepad/gamepad_test.py --calibrate
```
Saves axis ranges to `tools/gamepad/calibrations/`.

**Step 6 — Validate all inputs**
```bash
python3 tools/gamepad/gamepad_test.py
```

Control mapping:
- Left stick X → steer, Left stick Y → throttle (+fwd / −rev)
- L1 held → arm (dead-man)
- L1 + D-pad diagonal → e-stop (latched)
- Both stick clicks (LS + RS) → re-arm

---

## Sharing the Dashboard for a Demo (ngrok)

ngrok creates a temporary public HTTPS URL that forwards to the dashboard.
The URL is valid for the duration of one session — it changes every time ngrok restarts.
This is intentional: the robot is only accessible while you are actively running a demo.

**First time only — install ngrok on the Jetson:**
```bash
bash tools/setup/ngrok_setup.sh
```
Requires a free account at [dashboard.ngrok.com](https://dashboard.ngrok.com/signup).

**Each demo session (after camera and dashboard are running):**
```bash
ngrok http 8082
```

ngrok prints the session URL — copy and share it with your audience. Stop with `Ctrl+C` when the demo ends.

> **Warning:** Anyone with the URL can operate the drive controls. Only share it with people you trust, and stop the tunnel immediately after the demo.

---

## Running the Operator Dashboard

The dashboard requires two processes running on the Jetson:

**First time only — set up stable camera device path:**
```bash
sudo python3 tools/camera/setup_udev.py
```
This installs a udev rule so the camera always appears at `/dev/robot_camera` regardless of USB enumeration order.

**Terminal 1 — camera stream:**
```bash
python3 tools/camera/camera_test.py
```

**Terminal 2 — dashboard:**
```bash
python3 tools/dashboard/server.py
```

Then open `http://<jetson-ip>:8082` in a browser.

The dashboard polls the camera's `/health` endpoint every 2 seconds and auto-reconnects the stream on both hardware and software disconnections without requiring a page refresh.

---

## Prerequisites

### 1) arduino-cli binary (x86)

The `bin/arduino-cli` binary is **not tracked in git** (36 MB). Place it manually:

```bash
# Download from the official release page
# https://arduino.github.io/arduino-cli/latest/installation/

    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh

# Download the linux_amd64 build and place the binary at bin/arduino-cli

    chmod +x bin/arduino-cli

```

If setting up arduino-cli from scratch, install the ROBOTIS OpenCM9.04 board package:

```bash
./bin/arduino-cli config init
./bin/arduino-cli config set board_manager.additional_urls \
  https://raw.githubusercontent.com/ROBOTIS-GIT/OpenCM9.04/master/arduino/opencm_release/package_opencm9.04_index.json
./bin/arduino-cli core update-index
./bin/arduino-cli core install OpenCM904:OpenCM904
```

### 2) Dynamixel2Arduino library

The `Dynamixel2Arduino` library is required by all Dynamixel-related sketches (`dxl_commander`, `dxl_id_scan`, etc.). Install it via arduino-cli:

```bash
./bin/arduino-cli lib update-index
./bin/arduino-cli lib install "Dynamixel2Arduino"
```

This installs the library into the Arduino sketchbook libraries directory (typically `~/Arduino/libraries/`). The board package already bundles `DynamixelSDK` and `DynamixelWorkbench`, but **`Dynamixel2Arduino` must be installed separately** as shown above.

## Workflow

### Step 1 — Compile a sketch (x86)

```bash
python build.py
```

Select a sketch from the menu. The compiled `.bin` is written to:

```
firmware/<sketch_name>/build/OpenCM904.OpenCM904.OpenCM904/<sketch_name>.ino.bin
```

### Step 2 — Set up the ARM host (one time)

Run on the ARM device:

```bash
sudo ./tools/remote_update/setup_arm_opencm_ssh_flasher.sh
```

This installs the OpenCM uploader binary and a udev rule that creates a stable `/dev/opencm` alias.

### Step 3 — Flash over SSH (x86)

Run the flash script — it scans for compiled `.bin` files and lets you pick one:

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh
```

You will be prompted for the SSH password. The script retries automatically for up to 20 seconds to handle bootloader timing.

Set defaults (ARM host, port, timeout) in `tools/remote_update/flash.conf` to avoid passing flags every time. See `tools/remote_update/README.md` for all options.

### Step 4 — Monitor data on x86

Once the firmware is flashed and running, stream the serial output from ARM to your x86 terminal:

```bash
./tools/monitor/serial_monitor.sh
```

## Repository Layout

```
bin/
  arduino-cli                  x86 Linux binary — not tracked, place manually
build.py                       Compile script (runs arduino-cli)
firmware/
  <sketch_name>/
    <sketch_name>.ino          Arduino sketch
    build/                     Compiled artifacts — gitignored
  esp32_hci/                   ESP-IDF project — ESP32-C6 BLE HCI controller firmware
    main/main.c                BLE controller-only mode, UART0 115200 baud
    sdkconfig.defaults         ESP32-C6 BT/HCI config (BT_CTRL_* options)
    CMakeLists.txt
tools/
  ackermann_ui/
    server.py                  Browser config tool — parameter tuning, steer calibration, servo state
    ackermann_config.json      Saved robot config (read by dashboard at runtime)
  dashboard/
    server.py                  Operator dashboard — WASD drive, camera feed, e-stop, live config
  camera/
    camera_test.py             Standalone MJPEG stream server with /health endpoint
    setup_udev.py              One-time udev rule installer — stable /dev/robot_camera symlink
  gamepad/
    gamepad_test.py            Live input validator — all axes/buttons, drive preview, calibration
    setup_gamepad.sh           Interactive BT pairing — scan, pair, trust, connect
    setup_esp32_hci.sh         Register ESP32-C6 as hci0; install systemd service (Jetson)
    flash_esp32.sh             Build & flash ESP32-C6 HCI firmware via ESP-IDF (x86)
    calibrations/              Per-controller axis calibration JSON files
  setup/
    ngrok_setup.sh             Install ngrok and configure auth token (one-time, Jetson)
  dynamixel/                   Jetson-side Python tools (scan, nudge, monitor servos)
  remote_update/
    flash.conf                 Default arguments for the flash script
    x86_flash_opencm_bin_via_ssh.sh   Scan .bin files, pick one, flash via SSH
    setup_arm_opencm_ssh_flasher.sh   One-time ARM/Jetson setup
    README.md                  Detailed remote-flash instructions
  monitor/
    serial_monitor.sh          Interactive serial monitor (local or remote via SSH)
    monitor.conf               Known Jetson SSH addresses and serial ports
```

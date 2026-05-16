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

### Robot Agent — Jetson (`software/robot/`)

- [x] Serial driver — binary frame encode/decode, background recv thread, CRC validation (`serial_driver.py`)
- [x] Ackermann kinematics — 4WS counter-phase geometry, per-wheel speed differential (`ackermann.py`)
- [x] E-stop handler — WebSocket silence detection (500 ms), zero-speed frame dispatch (`estop.py`)
- [x] Camera generator — V4L2 capture, MJPEG encode, background thread (`camera.py`)
- [x] Robot Agent server — WebSocket drive commands, MJPEG `/stream`, JSON `/status` (`main.py`)
- [x] Command translator — gamepad axes [-1…1] → physical (v m/s, δ deg)

### Operator Dashboard (`docs/dashboard/`)

- [ ] Single-page HTML dashboard (Vanilla JS, no build step)
- [ ] Gamepad API polling at 50 Hz → WebSocket drive commands
- [ ] Keyboard fallback control (arrow keys / WASD)
- [ ] Live MJPEG camera feed (`<img src="/stream">`)
- [ ] Servo status panel (voltages, temperatures, positions, modes)
- [ ] E-stop indicator and manual E-stop button
- [ ] WebSocket reconnect on connection loss

### Infrastructure

- [x] Remote firmware flash toolchain — compile x86, flash to OpenCM9.04 over SSH (`tools/remote_update/`)
- [x] Serial monitor — stream OpenCM debug output from Jetson to x86 (`tools/monitor/`)
- [ ] Tailscale VPN setup — stable robot addressing, NAT traversal (external, manual step)
- [ ] Systemd service units — auto-start Robot Agent on Jetson boot

### Calibration & Commissioning Tools (not in v1 architecture, added during development)

- [x] Ackermann UI — browser-based parameter tuning, bird's-eye visualisation, servo state (`tools/ackermann_ui/`)
- [x] Servo ID identification — nudge each servo and map to wheel role (`tools/dynamixel/dxl_identify.py`)
- [x] Steer centre calibration — torque-off, physically align, record neutral offsets
- [x] Trapezoidal steering profile — smooth motion on large angle commands, drag vs. click detection
- [x] Camera test server — standalone MJPEG stream over stdlib HTTP, no serial port needed (`tools/camera/camera_test.py`)

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
tools/
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

# Black-Mata

x86 build toolchain and Jetson runtime tools for the Black-Mata robot.
Firmware is compiled on x86, flashed to the OpenCM9.04 over SSH, and the Jetson runs Python tools to communicate with the board.

## Prerequisites

### 1) arduino-cli binary (x86)

The `bin/arduino-cli` binary is **not tracked in git** (36 MB). Place it manually:

```bash
# Option A — copy from an existing Mata-mata checkout
cp /path/to/Mata-mata/bin/arduino-cli bin/arduino-cli

# Option B — download from the official release page
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

### 3) Dynamixel2Arduino library

The `Dynamixel2Arduino` library is required by all Dynamixel-related sketches (`dxl_commander`, `dxl_id_scan`, etc.). Install it via arduino-cli:

```bash
./bin/arduino-cli lib update-index
./bin/arduino-cli lib install "Dynamixel2Arduino"
```

This installs the library into the Arduino sketchbook libraries directory (typically `~/Arduino/libraries/`). The board package already bundles `DynamixelSDK` and `DynamixelWorkbench`, but **`Dynamixel2Arduino` must be installed separately** as shown above.

### 2) SSH key access to the ARM host

Passwordless SSH must be configured to the target device. Verify with:

```bash
ssh <user>@<arm-host> echo ok
```

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

You will be prompted for the SSH password **once**. The script retries automatically for up to 20 seconds to handle bootloader timing.

Set defaults (ARM host, port, timeout) in `tools/remote_update/flash.conf` to avoid passing flags every time. See `tools/remote_update/README.md` for all options.

### Step 4 — Monitor IMU data on x86

Once the IMU firmware is flashed and running, stream the serial output from ARM to your x86 terminal:

```bash
./tools/monitor/serial_monitor.sh
```

## Sketches

| Sketch | Description |
|---|---|
| `dxl_commander` | Dynamixel command/response only |
| `dxl_imu_commander` | **Dynamixel + BNO080 IMU combined** (recommended) |
| `imu_bno080_spi` | IMU stream only |
| `dxl_id_scan` | Scan bus for servo IDs |
| `dxl_id_change` | Change a servo's ID |
| `dxl_servo_monitor` | Servo state monitor |
| `dxl_servo_nudge` | Move a servo by a small angle |
| `dxl_u2d2_bridge` | USB↔Dynamixel bridge (U2D2) |
| `opencm_blink` | Blink LED (sanity check) |
| `spi_scanner` | Scan SPI bus |

### dxl_imu_commander

Combines all Dynamixel commands with a non-blocking BNO080 IMU stream over a single USB serial connection at 115200 baud. The IMU streams continuously at 20 Hz (configurable). DXL command responses are interleaved on the same line-oriented protocol.

**Additional wiring (BNO080 SPI1):**

| BNO080 | OpenCM9.04 pin |
|---|---|
| CS | 11 |
| SCK | 1 |
| MISO | 6 |
| MOSI | 7 |
| INT | 12 |
| RST | 13 |
| WAK | 14 |

**Additional library required:**

```bash
./bin/arduino-cli lib install "SparkFun BNO080 Cortex Based IMU"
```

**Output line format (all prefixed, CSV, newline-terminated):**

```
# ...                               comments / info
QUAT,<ms>,<i>,<j>,<k>,<real>,<rad_accuracy>
ACCEL,<ms>,<x>,<y>,<z>             m/s^2
GYRO,<ms>,<x>,<y>,<z>              rad/s
LINACC,<ms>,<x>,<y>,<z>            m/s^2, gravity removed
GRAV,<ms>,<x>,<y>,<z>              m/s^2
MAG,<ms>,<x>,<y>,<z>               uTesla
OK,<CMD>,...                        DXL command response
ERR,<CMD>,...                       DXL error
FOUND,<id>,...                      from SCAN
STATUS,<ms>,<id>,...                from MONITOR
```

**IMU commands:**

```
IMUON               Enable IMU streaming (on by default)
IMUOFF              Disable IMU streaming
IMURATE <ms>        Set report interval in ms (default: 50 = 20 Hz)
```

All Dynamixel commands from `dxl_commander` are available unchanged (`PING`, `SCAN`, `MONITOR`, `SETPOS`, etc. — send `HELP` for the full list).

**Minimal Python reader example (Jetson Nano):**

```python
import serial

ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)

for line in ser:
    line = line.decode('ascii', errors='ignore').strip()
    if not line or line.startswith('#'):
        continue
    parts = line.split(',')
    kind = parts[0]
    if kind == 'QUAT':
        ts, i, j, k, real, acc = parts[1:]
        print(f"quat t={ts}ms  i={i} j={j} k={k} real={real}")
    elif kind == 'ACCEL':
        ts, x, y, z = parts[1:]
        print(f"accel t={ts}ms  x={x} y={y} z={z}")
    elif kind.startswith('OK') or kind.startswith('ERR'):
        print('DXL:', line)
```

To also send commands while reading:

```python
ser.write(b'SCAN\n')          # scan for servos
ser.write(b'GETPOS 1\n')      # read servo 1 position
ser.write(b'IMURATE 100\n')   # slow IMU to 10 Hz
ser.write(b'IMUOFF\n')        # stop IMU stream
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

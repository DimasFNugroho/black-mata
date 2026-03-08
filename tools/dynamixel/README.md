# tools/dynamixel — Python DynamixelSDK Tools

Python tools for controlling AX-12A servos from **x86 or ARM** via a
**OpenCM9.04 running `dxl_u2d2_bridge.ino`** (transparent USB-to-Dynamixel bridge).

## Prerequisites

### 1. Flash the bridge firmware

Flash `firmware/dxl_u2d2_bridge/dxl_u2d2_bridge.ino` to the OpenCM9.04.
This makes the OpenCM behave like a U2D2 — a transparent USB-to-Dynamixel passthrough.

### 2. Install Python dependencies

Install the Dynamixel SDK Python package:

```bash
pip install dynamixel-sdk
```

If `pip` is not available, install it first:

```bash
# Ubuntu / Debian
sudo apt install python3-pip

# Then install the SDK
pip3 install dynamixel-sdk
```

Verify the install:

```bash
python3 -c "from dynamixel_sdk import PortHandler; print('OK')"
```

Alternatively, if using the project's `pyproject.toml`:

```bash
pip install -e .
```

### 3. Find the serial port

| Platform | Typical port         |
|----------|----------------------|
| Linux    | `/dev/ttyACM0`       |
| macOS    | `/dev/tty.usbmodemXX`|
| Windows  | `COM3` (Device Manager)|

---

## Tools

### `dxl_scan.py` — Scan for servos

Scans IDs 1–252 and reports each servo's ID, model, and mode.

```bash
python3 dxl_scan.py
python3 dxl_scan.py --port /dev/ttyACM0
python3 dxl_scan.py --all-bauds          # also try 57600, 115200, 19200, 9600
```

### `dxl_monitor.py` — Stream servo state

Continuously polls a servo and prints CSV to stdout.

```bash
python3 dxl_monitor.py --id 1
python3 dxl_monitor.py --id 1 --interval 0.1   # 10 Hz
```

**Output format:**

```
STATUS,<ms>,<id>,<mode>,<position_deg>,<speed_rpm>,<load_pct>,<voltage_V>,<temp_C>
```

| Field          | Unit    | Description                             |
|----------------|---------|-----------------------------------------|
| `ms`           | ms      | Time since start                        |
| `id`           | —       | Servo ID                                |
| `mode`         | —       | `JOINT` or `WHEEL`                      |
| `position_deg` | degrees | 0–300° (AX-12A range)                   |
| `speed_rpm`    | RPM     | Present speed (magnitude)               |
| `load_pct`     | %       | Present load (0–100%)                   |
| `voltage_V`    | V       | Present input voltage                   |
| `temp_C`       | °C      | Present internal temperature            |

### `dxl_nudge.py` — Nudge servo position

Moves the servo +N degrees, waits, then returns to origin. Repeats on an interval.
Automatically handles WHEEL mode (saves mode → switches to JOINT → nudges → restores).

```bash
python3 dxl_nudge.py --id 1
python3 dxl_nudge.py --id 1 --nudge 10 --speed 300 --interval 5
python3 dxl_nudge.py --id 1 --once     # run one cycle then exit
```

| Option       | Default | Description                        |
|--------------|---------|------------------------------------|
| `--nudge`    | 5.0     | Degrees to nudge (positive = CCW)  |
| `--speed`    | 200     | Goal speed in ticks (1–1023)       |
| `--interval` | 3.0     | Seconds between nudge cycles       |
| `--once`     | off     | Run a single cycle and exit        |

### `dxl_id_change.py` — Change servo ID

Changes a servo's ID, with conflict detection and EEPROM write verification.

```bash
python3 dxl_id_change.py --current 1 --new 5
```

> **Warning:** Do not power off the servo during the EEPROM write.

---

## Common options (all tools)

| Option    | Default         | Description          |
|-----------|-----------------|----------------------|
| `--port`  | `/dev/ttyACM0`  | Serial port          |
| `--baud`  | `1000000`       | Baud rate            |

---

## How it works

```
x86 / ARM host
  └─ Python script (DynamixelSDK)
       └─ USB serial (/dev/ttyACM0)
            └─ OpenCM9.04 (dxl_u2d2_bridge.ino)
                 └─ 3-wire TTL Dynamixel bus
                      └─ AX-12A servos
```

The bridge firmware transparently forwards Dynamixel packets in both directions,
switching the half-duplex TTL bus direction based on a timeout heuristic.

# tools/dynamixel — Dynamixel Servo Tools (ARM)

Python tools for controlling AX-12A servos directly on the ARM host (Jetson)
via an OpenCM9.04 running `dxl_commander.ino`.

The OpenCM handles all Dynamixel bus communication internally.
Python scripts send simple text commands over USB Serial — no DynamixelSDK needed.

## Prerequisites

### 1. Flash the commander firmware

Flash `firmware/dxl_commander/dxl_commander.ino` to the OpenCM9.04 from the x86 branch.

### 2. Install Python dependency

```bash
pip3 install pyserial
```

Verify:

```bash
python3 -c "import serial; print('OK')"
```

### 3. Serial port

The scripts auto-detect the port in this order:

1. `/dev/opencm`
2. `/dev/serial/by-id/*ROBOTIS*`
3. `/dev/ttyACM*`

Override with `--port /dev/ttyACM0` if needed.

---

## Tools

### `dxl_scan.py` — Scan for servos

Scans IDs 1-252 and reports each servo's ID, model, firmware version, and mode.

```bash
python3 dxl_scan.py
python3 dxl_scan.py --max-id 30
```

### `dxl_monitor.py` — Stream servo state

Continuously streams a servo's state as CSV to stdout.

```bash
python3 dxl_monitor.py --id 1
python3 dxl_monitor.py --id 1 --interval 100   # 100ms = 10 Hz
```

**Output format:**

```
STATUS,<ms>,<id>,<mode>,<pos_deg>,<speed_rpm>,<load_pct>,<voltage_V>,<temp_C>
```

| Field          | Unit    | Description                             |
|----------------|---------|-----------------------------------------|
| `ms`           | ms      | Time since monitor started              |
| `id`           |         | Servo ID                                |
| `mode`         |         | `JOINT` or `WHEEL`                      |
| `pos_deg`      | degrees | 0-300 (AX-12A range)                    |
| `speed_rpm`    | RPM     | Present speed (magnitude)               |
| `load_pct`     | %       | Present load (0-100%)                   |
| `voltage_V`    | V       | Present input voltage                   |
| `temp_C`       | C       | Present internal temperature            |

### `dxl_nudge.py` — Nudge servo position

Moves the servo +N degrees, waits, then returns to origin.
Automatically handles WHEEL mode (saves, switches to JOINT, nudges, restores).

```bash
python3 dxl_nudge.py --id 1
python3 dxl_nudge.py --id 1 --nudge 10 --speed 300
python3 dxl_nudge.py --id 1 --repeat 5 --interval 2
```

| Option       | Default | Description                        |
|--------------|---------|------------------------------------|
| `--nudge`    | 5.0     | Degrees to nudge (positive = CCW)  |
| `--speed`    | 200     | Goal speed in ticks (1-1023)       |
| `--repeat`   | 1       | Number of nudge cycles             |
| `--interval` | 3.0     | Seconds between cycles             |

### `dxl_id_change.py` — Change servo ID

Changes a servo's ID, with conflict detection and EEPROM write verification.

```bash
python3 dxl_id_change.py --current 1 --new 5
```

> **Warning:** Do not power off the servo during the EEPROM write.

### `dxl_position.py` — Read or set position

```bash
python3 dxl_position.py --id 1                  # read
python3 dxl_position.py --id 1 --set 512         # move to tick 512
python3 dxl_position.py --id 1 --set 512 --speed 100
```

### `dxl_speed.py` — Read or set speed

```bash
python3 dxl_speed.py --id 1                # read
python3 dxl_speed.py --id 1 --set 200      # set
```

### `dxl_mode.py` — Read or set operating mode

```bash
python3 dxl_mode.py --id 1                  # read current mode
python3 dxl_mode.py --id 1 --set WHEEL      # switch to wheel mode
python3 dxl_mode.py --id 1 --set JOINT      # switch to joint mode
```

### `dxl_voltage.py` — Read servo voltage

```bash
python3 dxl_voltage.py                # all servos
python3 dxl_voltage.py --id 1         # single servo
python3 dxl_voltage.py --max-id 30    # scan up to ID 30
```

---

## Common options (all tools)

| Option    | Default        | Description                   |
|-----------|----------------|-------------------------------|
| `--port`  | auto-detect    | Serial port                   |
| `--baud`  | `115200`       | USB baud rate to OpenCM       |

---

## How it works

```
ARM host (Jetson)
  └─ Python script (pyserial)
       └─ Text commands over USB serial (115200 baud)
            └─ OpenCM9.04 (dxl_commander.ino)
                 └─ Dynamixel2Arduino (native half-duplex)
                      └─ 3-wire TTL Dynamixel bus
                           └─ AX-12A servos
```

The commander firmware receives text commands (e.g. `SCAN`, `MONITOR 1 200`),
handles all Dynamixel protocol communication internally, and returns structured
CSV responses over USB Serial.

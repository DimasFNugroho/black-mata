# serial_rw_example

Binary serial communication example between the OpenCM9.04 (MCU) and the
Jetson Nano (host). Demonstrates fixed-length binary packets, CRC-16 validation,
and a HEARTBEAT/ACK health-check flow.

## Files

| File | Side | Description |
|------|------|-------------|
| `firmware/serial_rw_example/serial_rw_example.ino` | MCU | Receives HEARTBEAT, replies HB_ACK, blinks LED |
| `scripts/serial_rw_example.py` | Jetson | Sends HEARTBEAT, validates ACK, prints status |

## Communication flow

```
Jetson                          OpenCM9.04
  |                                  |
  |--- HEARTBEAT (seq=N) ----------->|
  |                                  |  validate CRC
  |                                  |  blink LED @ 1 Hz
  |<-- HB_ACK    (seq=N) ------------|
  |                                  |
  validate CRC                       |
  print "Healthy"                    |
  |                                  |
  | (repeat every --interval seconds)|
```

## Packet format

Fixed length: **9 bytes**. All fields are `uint8_t` — no padding, no alignment issues.

| Byte | Field     | Value / Type        | Notes                        |
|------|-----------|---------------------|------------------------------|
| 0    | `START`   | `0xAA`              | Frame marker                 |
| 1    | `CMD`     | `uint8`             | `0x01` = HEARTBEAT, `0x02` = HB_ACK |
| 2    | `SEQ`     | `uint8`             | Rolling counter 0–255        |
| 3–6  | `PAYLOAD` | `uint8[4]`          | Reserved (zeros)             |
| 7    | `CRC_HI`  | `uint8`             | High byte of CRC-16          |
| 8    | `CRC_LO`  | `uint8`             | Low byte of CRC-16           |

**CRC:** CRC-16 CCITT — polynomial `0x1021`, initial value `0xFFFF`, computed
over bytes `[0..6]` (everything except the CRC bytes themselves).

## How to build and flash (MCU)

```bash
# From repo root
python build.py
# Select: serial_rw_example
```

Or directly with arduino-cli:

```bash
bin/arduino-cli compile --fqbn OpenCM904:OpenCM904:OpenCM904 firmware/serial_rw_example
bin/arduino-cli upload  --fqbn OpenCM904:OpenCM904:OpenCM904 --port /dev/ttyACM0 firmware/serial_rw_example
```

## How to run (Jetson)

```bash
python3 scripts/serial_rw_example.py --port /dev/opencm
```

Options:

| Flag         | Default        | Description               |
|--------------|----------------|---------------------------|
| `--port`     | *(required)*   | Serial port               |
| `--baud`     | `115200`       | Baud rate                 |
| `--interval` | `2.0`          | Heartbeat interval (s)    |

### Expected output

```
Connected : /dev/opencm @ 115200 baud
Interval  : 2.0s

[TX] HEARTBEAT    seq=  0  raw=aa 01 00 00 00 00 00 f0 b8
[RX] HB_ACK       seq=  0  raw=aa 02 00 00 00 00 00 f0 ab
     Status : Healthy
```

If no response arrives within 1 second:
```
[RX] Timeout — no response (got 0 bytes)
```

## MCU behaviour

| State | LED |
|-------|-----|
| Waiting for first heartbeat | Off |
| Heartbeat received and valid | Blinking at 1 Hz |

The LED blink is non-blocking (millis-based), so the MCU continues receiving
packets while blinking.

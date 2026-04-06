# Monitor Tools

Stream raw serial output from the OpenCM9.04.

## serial_monitor.sh

A single script that supports two modes:

| Mode | Run on | How |
|---|---|---|
| `serial_monitor.sh` (default) | x86 | SSH into Jetson, forward serial stream to x86 terminal |
| `serial_monitor.sh --local` | Jetson | Read directly from local serial port |

Mode is auto-detected: **remote** if `ARM_HOST` is configured in `flash.conf`, otherwise **local**.

## Configuration

Edit `monitor.conf` (same directory) to add known Jetson SSH addresses and serial ports:

```bash
KNOWN_HOSTS=(
    "mata-mata@192.168.1.50"
)

KNOWN_PORTS=(
    "/dev/opencm"
    "/dev/ttyACM0"
)
```

## Usage

Just run the script — it will prompt for everything:

```bash
./tools/monitor/serial_monitor.sh
```

Example session (remote mode):

```
Serial Monitor — OpenCM9.04
===========================
  1) Local  — read directly from serial port (run on Jetson)
  2) Remote — stream via SSH from Jetson (run on x86)

Select mode [1/2]: 2

Select Jetson SSH address:
  1) mata-mata@192.168.1.50

Select [1-1]: 1

Select serial port on Jetson:
  1) /dev/opencm
  2) /dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00
  3) /dev/ttyACM0

Select [1-3]: 1

Connecting to mata-mata@192.168.1.50...
Streaming from mata-mata@192.168.1.50  (Ctrl+C to stop)
---
```

In local mode, the script also auto-detects currently connected serial devices and merges them with the list in `monitor.conf`.

## Configuration

Set defaults in `tools/remote_update/flash.conf`:

```bash
ARM_HOST="user@arm-ip"
ARM_PORT="/dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00"
```

When `ARM_HOST` is set, the script defaults to remote mode automatically.

## Serial port auto-detection

If `--port` is omitted (or the specified port is not found), the script tries in order:

1. `/dev/opencm` (udev alias set by `setup_arm_opencm_ssh_flasher.sh`)
2. `/dev/serial/by-id/*ROBOTIS*`
3. first `/dev/ttyACM*`
4. first `/dev/ttyUSB*`

## Expected output

Whatever the OpenCM9.04 sends over its USB serial port is printed as-is.
For example, with the `imu_bno080_spi` firmware:

```
# Reading from /dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00 at 115200 baud
---
QUAT,1234,0.001234,-0.002345,0.003456,0.999900,0.012300
ACCEL,1234,0.0123,9.8100,0.0045
GYRO,1234,0.0001,-0.0002,0.0003
LINACC,1234,0.0120,-0.0010,0.0040
GRAV,1234,0.0001,9.8099,0.0005
MAG,1234,23.1200,-4.5600,38.9900
```

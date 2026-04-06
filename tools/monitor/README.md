# Monitor Tools

Stream raw serial output from the OpenCM9.04.

## serial_monitor.sh

A single script that supports two modes:

| Mode | Run on | How |
|---|---|---|
| `serial_monitor.sh` (default) | x86 | SSH into Jetson, forward serial stream to x86 terminal |
| `serial_monitor.sh --local` | Jetson | Read directly from local serial port |

Mode is auto-detected: **remote** if `ARM_HOST` is configured in `flash.conf`, otherwise **local**.

## Usage

Just run the script — it will prompt you for everything:

```bash
./tools/monitor/serial_monitor.sh
```

Example session:

```
Serial Monitor — OpenCM9.04
===========================
 1) Local  — read directly from serial port (run on Jetson)
 2) Remote — stream via SSH from Jetson (run on x86)

Select mode [1/2]: 2

Jetson SSH address [mata-mata@192.168.1.50]:
Serial port on Jetson [/dev/opencm]:

Connecting to mata-mata@192.168.1.50...
Streaming from mata-mata@192.168.1.50  (Ctrl+C to stop)
---
QUAT,1234,...
```

Known values from `flash.conf` are shown as defaults — just press Enter to accept them.

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

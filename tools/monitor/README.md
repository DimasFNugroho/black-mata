# Monitor Tools (x86)

Tools for reading live data from the OpenCM9.04 over SSH from an x86 machine.
The OpenCM is physically connected to the ARM host via USB serial. These scripts
SSH into ARM and forward the serial stream to your x86 terminal — no need to be
physically near the robot.

## How it works

```
OpenCM9.04 --[USB serial]--> ARM host --[SSH]--> x86 terminal
```

Everything the OpenCM prints over `Serial` (e.g. sensor readings, debug output)
is streamed live to your screen.

## Files

- `read_imu.sh`: stream BNO080 IMU data from the OpenCM to your x86 terminal.

## read_imu.sh

### Usage

```bash
cd tools/monitor
./read_imu.sh
```

You will be prompted for the SSH password **once**. The serial port on ARM is
auto-detected if the configured one is not found.

### Configuration

`read_imu.sh` reads `ARM_HOST` and `ARM_PORT` from `tools/remote_update/flash.conf`.
Set them there to avoid passing flags every time:

```bash
ARM_HOST="user@arm-ip"
ARM_PORT="/dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00"
```

Override per-run with flags:

```bash
./read_imu.sh --arm-host user@arm-ip --arm-port /dev/ttyACM0 --baud 115200
```

### Serial port auto-detection

If the configured `ARM_PORT` is not found on ARM, the script falls back to:

1. `/dev/serial/by-id/*ROBOTIS*`
2. first `/dev/ttyACM*`
3. first `/dev/ttyUSB*`

### Expected output

```
# Reading from /dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00
# BNO080 IMU ready — streaming at 20 Hz
# FORMAT: TYPE,timestamp_ms,values...
QUAT,1234,0.001234,-0.002345,0.003456,0.999900,0.012300
ACCEL,1234,0.0123,9.8100,0.0045
GYRO,1234,0.0001,-0.0002,0.0003
LINACC,1234,0.0120,-0.0010,0.0040
GRAV,1234,0.0001,9.8099,0.0005
MAG,1234,23.1200,-4.5600,38.9900
```

### Data format

| Type   | Fields                              | Units     |
|--------|-------------------------------------|-----------|
| QUAT   | timestamp, i, j, k, real, accuracy  | rad       |
| ACCEL  | timestamp, x, y, z                  | m/s²      |
| GYRO   | timestamp, x, y, z                  | rad/s     |
| LINACC | timestamp, x, y, z                  | m/s² (gravity removed) |
| GRAV   | timestamp, x, y, z                  | m/s²      |
| MAG    | timestamp, x, y, z                  | µTesla    |

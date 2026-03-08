# imu_bno080_spi

Reads all sensor reports from a BNO080 IMU over SPI1 on OpenCM9.04 and
streams them as CSV lines over USB Serial at 20 Hz.

## Wiring (OpenCM9.04 → BNO080)

| OpenCM9.04 Pin | BNO080 Pin | Function |
|:--------------:|:----------:|----------|
| 11             | CS         | Chip Select |
| 1              | SCK        | SPI Clock |
| 6              | MISO       | Master In Slave Out |
| 7              | MOSI       | Master Out Slave In |
| 12             | INT        | Interrupt (data ready) |
| 13             | RST        | Reset |
| 14             | WAK        | Wake (PS0) |
| 3.3V           | VCC        | Power |
| GND            | GND        | Ground |

## Hardware prerequisite

The SparkFun BNO080 breakout defaults to **I2C mode**. Before flashing:

1. Flip the board over to the back side
2. Find the **PS1 jumper**
3. Add a solder blob to bridge it — this pulls PS1 HIGH, enabling SPI mode

Without this, `beginSPI()` will fail even if all pins are correct.

## Library

```bash
./bin/arduino-cli lib install "SparkFun BNO080 Cortex Based IMU"
```

## Output format

Each line is a CSV record: `TYPE,timestamp_ms,values...`

Lines starting with `#` are informational comments, not data.

### QUAT — Rotation Vector (quaternion)

```
QUAT,<ms>,<i>,<j>,<k>,<real>,<rad_accuracy>
```

| Field         | Unit | Description |
|---------------|------|-------------|
| i, j, k, real | —   | Quaternion components describing 3D orientation |
| rad_accuracy  | rad  | Estimated heading accuracy |

Example:
```
QUAT,7451,0.053589,-0.026245,0.958618,0.278381,3.141602
```

### ACCEL — Raw Accelerometer

```
ACCEL,<ms>,<x>,<y>,<z>
```

| Field | Unit  | Description |
|-------|-------|-------------|
| x,y,z | m/s² | Total acceleration including gravity |

At rest, the Z axis should read ~9.81 m/s² (gravity).

Example:
```
ACCEL,7451,1.1094,-0.1914,9.6523
```

### GYRO — Gyroscope

```
GYRO,<ms>,<x>,<y>,<z>
```

| Field | Unit  | Description |
|-------|-------|-------------|
| x,y,z | rad/s | Angular velocity around each axis |

Near zero when the board is stationary.

Example:
```
GYRO,7451,0.0000,0.0000,0.0000
```

### LINACC — Linear Acceleration

```
LINACC,<ms>,<x>,<y>,<z>
```

| Field | Unit  | Description |
|-------|-------|-------------|
| x,y,z | m/s² | Acceleration with gravity removed (motion only) |

Near zero when stationary. Use this for detecting actual movement.

Example:
```
LINACC,7451,0.0000,0.0000,0.0000
```

### GRAV — Gravity Vector

```
GRAV,<ms>,<x>,<y>,<z>
```

| Field | Unit  | Description |
|-------|-------|-------------|
| x,y,z | m/s² | Direction and magnitude of gravity in the sensor frame |

Useful for determining the orientation of the board relative to the ground.
Should always sum to ~9.81 m/s² in magnitude.

Example:
```
GRAV,7451,1.1406,-0.1953,9.6641
```

### MAG — Magnetometer

```
MAG,<ms>,<x>,<y>,<z>
```

| Field | Unit   | Description |
|-------|--------|-------------|
| x,y,z | µTesla | Magnetic field strength along each axis |

Reflects the local magnetic field (Earth + any nearby interference).

Example:
```
MAG,7446,24.7500,-45.4375,-25.5625
```

## Reading data on x86

With the OpenCM connected to the Jetson via USB, stream data directly
to your x86 terminal over SSH:

```bash
./tools/monitor/read_imu.sh
```

See `tools/monitor/README.md` for configuration and options.

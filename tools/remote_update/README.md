# Remote Flash Workflow (x86 -> ARM over SSH)

Flashes a compiled `.bin` from x86 to an OpenCM9.04 that is physically
connected to an ARM host, over SSH. No need to be near the robot.

## Files

- `x86_flash_opencm_bin_via_ssh.sh`: selects a firmware file and flashes it to the OpenCM via SSH.
- `setup_arm_opencm_ssh_flasher.sh`: one-time ARM setup — installs the OpenCM uploader and udev rule on the Jetson.
- `flash.conf`: default configuration (ARM host, serial port, timeout).

## ARM setup (one time, run on Jetson)

```bash
cd tools/remote_update
sudo ./setup_arm_opencm_ssh_flasher.sh
```

This installs:
- `/usr/local/bin/opencm9.04_ld_armhf` — OpenCM uploader binary
- `/etc/udev/rules.d/99-opencm.rules` — stable `/dev/opencm` port alias

## Configuration

Edit `flash.conf` to set your defaults:

```bash
ARM_HOST="user@arm-ip"
ARM_PORT="/dev/serial/by-id/usb-CM-900_ROBOTIS_Virtual_COM_Port-if00"
FLASH_TIMEOUT="20"
```

All values can be overridden per-run with CLI flags (see `--help`).

## Flash from x86

Run the script from the project root — it scans for compiled `.bin` files
and presents a menu:

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh
```

Example session:

```
Available firmware:
  1. firmware/imu_bno080_spi/build/.../imu_bno080_spi.ino.bin
  2. firmware/opencm_blink/build/.../opencm_blink.ino.bin
Enter number [1-2]: 1

[0/3] Opening SSH connection to mata-mata@100.111.193.124
mata-mata@100.111.193.124's password:        ← only once
[1/3] Copying bin to ARM ...
[2/3] Running uploader on ARM host (timeout: 20s)
Attempt 1 (0s elapsed) ...
...
[3/3] Flash complete.
```

You are asked for the SSH password **once**. All retries and the file transfer
reuse the same connection via SSH ControlMaster.

To skip the menu and flash a specific file directly:

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh --bin /path/to/firmware.bin
```

## Serial port auto-detection

If `ARM_PORT` is not set or the device is not found, the script falls back to:

1. `/dev/serial/by-id/*ROBOTIS*`
2. first `/dev/ttyACM*`
3. first `/dev/ttyUSB*`

## Bootloader timing

When the OpenCM already has firmware running, the first upload attempt may
fail as the board transitions into bootloader mode. The script automatically
retries until it succeeds or the timeout is reached (default: 20s).

If it consistently fails, increase the timeout:

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh --timeout 40
```

## After flashing

To read live serial output from the OpenCM on your x86 machine:

```bash
./tools/monitor/serial_monitor.sh
```

See `tools/monitor/README.md` for details.

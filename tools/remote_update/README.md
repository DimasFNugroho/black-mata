# Remote Flash Workflow (x86 -> ARM over SSH)

Use this when OpenCM9.04 is physically connected to ARM and you want to flash an existing `.bin` from x86 without rebuilding.

## Files

- `setup_arm_opencm_ssh_flasher.sh`: installs ARM runtime dependency, official OpenCM uploader, and persistent `/dev/opencm` udev alias.
- `x86_flash_opencm_bin_via_ssh.sh`: copies `.bin` from x86 to ARM and runs the uploader remotely.

## 1) ARM setup (one time)

Run on ARM from this repository:

```bash
cd tools/remote_update
sudo ./setup_arm_opencm_ssh_flasher.sh
```

This installs:

- `/usr/local/bin/opencm9.04_ld_armhf`
- `/etc/udev/rules.d/99-opencm.rules`
- stable port alias: `/dev/opencm`

## 2) Flash from x86

Simply run the script — it will scan for compiled `.bin` files and present a menu:

```bash
cd tools/remote_update
./x86_flash_opencm_bin_via_ssh.sh
```

Example output:

```
Available firmware:
  1. firmware/imu_bno080_spi/build/.../imu_bno080_spi.ino.bin
  2. firmware/opencm_blink/build/.../opencm_blink.ino.bin
Enter number [1-2]:
```

You will be asked for the SSH password **once**. All retries and the file transfer reuse the same connection.

To skip the menu and flash a specific file directly:

```bash
./x86_flash_opencm_bin_via_ssh.sh --bin /path/to/firmware.bin
```

If `--arm-port` is omitted, the script auto-detects the serial port:

1. `/dev/serial/by-id/*ROBOTIS*`
2. first `/dev/ttyACM*`
3. first `/dev/ttyUSB*`

## Bootloader Timing Note

When OpenCM already has firmware, the first upload attempt may fail as the board transitions into bootloader mode. The script automatically retries for up to 20 seconds (configurable via `--timeout`).

## Notes

- Serial devices can re-enumerate; `ttyACM` index may change.
- Use stable `/dev/opencm` (or `/dev/serial/by-id/...`) instead of raw `ttyACM*`.
- If bootloader still needs USER+RESET, timing/retry helps but does not emulate button hold.

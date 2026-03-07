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

## 2) Flash from x86 (no rebuild)

```bash
cd tools/remote_update
./x86_flash_opencm_bin_via_ssh.sh \
  --arm-host <USER@ARM_IP> \
  --bin /absolute/path/to/opencm_blink.ino.bin \
  --arm-port /dev/opencm
```

If `--arm-port` is omitted, script auto-detects:

1. `/dev/serial/by-id/*ROBOTIS*`
2. first `/dev/ttyACM*`
3. first `/dev/ttyUSB*`

## Bootloader Timing Note

When OpenCM already has firmware and an upload attempt is made, the board may switch into bootloader mode.

In that bootloader state, immediate re-upload often fails. Wait about 10 seconds before starting the next upload attempt.

Recommended retry flow:

1. Attempt upload.
2. If it fails, wait 10 seconds.
3. Retry upload.

## Notes

- Serial devices can re-enumerate; `ttyACM` index may change.
- Use stable `/dev/opencm` (or `/dev/serial/by-id/...`) instead of raw `ttyACM*`.
- If bootloader still needs USER+RESET, timing/retry helps but does not emulate button hold.

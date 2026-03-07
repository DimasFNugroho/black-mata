# Black-Mata Remote Update Utilities

This repository contains OpenCM remote flashing utilities.

## Included Files

- `tools/remote_update/setup_arm_opencm_ssh_flasher.sh`: installs ARM-side uploader runtime and binary.
- `tools/remote_update/x86_flash_opencm_bin_via_ssh.sh`: copies `.bin` from x86 to ARM and flashes over SSH.
- `tools/remote_update/README.md`: end-to-end workflow instructions.

## Flashing Model

Arduino build/upload tooling is intentionally removed.
Flashing uses SSH forwarding workflow:

1. Install uploader on ARM host once.
2. Send any prebuilt `.bin` from x86 to ARM with `scp`.
3. Execute uploader on ARM serial port via `ssh`.

See `tools/remote_update/README.md` for exact commands.

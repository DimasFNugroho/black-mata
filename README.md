# Black-Mata x86 Toolchain

This branch adds an x86 Linux build toolchain on top of the existing remote-flash utilities.
The full workflow compiles OpenCM9.04 firmware on an x86 machine and flashes it to a remote ARM device over SSH.

## Prerequisites

### 1) arduino-cli binary (x86)

The `bin/arduino-cli` binary is **not tracked in git** (36 MB). Place it manually:

```bash
# Option A — copy from an existing Mata-mata checkout
cp /path/to/Mata-mata/bin/arduino-cli bin/arduino-cli

# Option B — download from the official release page
# https://arduino.github.io/arduino-cli/latest/installation/
# Download the linux_amd64 build and place the binary at bin/arduino-cli
chmod +x bin/arduino-cli
```

If setting up arduino-cli from scratch, install the ROBOTIS OpenCM9.04 board package:

```bash
./bin/arduino-cli config init
./bin/arduino-cli config set board_manager.additional_urls \
  https://raw.githubusercontent.com/ROBOTIS-GIT/OpenCM9.04/master/arduino/opencm_release/package_opencm9.04_index.json
./bin/arduino-cli core update-index
./bin/arduino-cli core install OpenCM904:OpenCM904
```

### 2) SSH key access to the ARM host

Passwordless SSH must be configured to the target device. Verify with:

```bash
ssh <user>@<arm-host> echo ok
```

## Workflow

### Step 1 — Compile a sketch (x86)

```bash
python build.py
```

Select a sketch from the menu. The compiled `.bin` is written to:

```
firmware/<sketch_name>/build/OpenCM904.OpenCM904.OpenCM904/<sketch_name>.ino.bin
```

### Step 2 — Set up the ARM host (one time)

Run on the ARM device:

```bash
sudo ./tools/remote_update/setup_arm_opencm_ssh_flasher.sh
```

This installs the OpenCM uploader binary and a udev rule that creates a stable `/dev/opencm` alias.

### Step 3 — Flash over SSH (x86)

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh \
  --arm-host <user>@<arm-ip> \
  --bin firmware/opencm_blink/build/OpenCM904.OpenCM904.OpenCM904/opencm_blink.ino.bin
```

Or configure defaults in `tools/remote_update/flash.conf` and run without arguments:

```bash
./tools/remote_update/x86_flash_opencm_bin_via_ssh.sh
```

See `tools/remote_update/README.md` for detailed flashing options and troubleshooting.

## Repository Layout

```
bin/
  arduino-cli                  x86 Linux binary — not tracked, place manually
build.py                       Compile script (runs arduino-cli)
firmware/
  <sketch_name>/
    <sketch_name>.ino          Arduino sketch
    build/                     Compiled artifacts — gitignored
tools/
  remote_update/
    flash.conf                 Default arguments for the flash script
    x86_flash_opencm_bin_via_ssh.sh   Copy .bin to ARM and flash via SSH
    setup_arm_opencm_ssh_flasher.sh   One-time ARM-side setup
    README.md                  Detailed remote-flash instructions
```

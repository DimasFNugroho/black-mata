# Black-Mata — Bluetooth Commissioning Procedure

Canonical, repeatable procedure to take **any** black-mata from a fresh OS image
to "an end-user can pair a gamepad from the dashboard with no terminal and no
sudo." Every step is idempotent and safe to re-run.

## The easy path

Run the guide, do the one action it prints, reboot if it says to, run it again —
repeat until it reports **fully commissioned**:

```bash
bash tools/gamepad/commission_bluetooth.sh
```

It is read-only and resumable across reboots: it inspects the current state and
tells you the single next step. The sections below are what it walks you
through, for reference and sign-off.

## Tiers (different lifetimes)

| Tier | What | Redone when | Script |
|------|------|-------------|--------|
| 0 Prereqs | dongle in; firmware bins (+kernel source) placed | per robot, manual | — |
| 1 Driver | RTL8761B `btrtl.ko` patch | per OS image / kernel update | `jetson_btrtl_8761b_fix.sh` |
| 2 Commission | ERTM, rfkill, AutoEnable, `bluetooth` group, service | once per robot | `setup_bluetooth_host.sh` |
| 3 Verify | all settings + cold-boot readiness | every time (gate) | `check_bluetooth_host.sh` |
| 4 Acceptance | pair from the dashboard, no terminal/sudo | once, to prove it | dashboard GAMEPAD pill |

A reboot separates tiers 1→2→3, which is why this is a guided procedure rather
than one unattended script.

---

## Tier 0 — Prerequisites

- The RTL8761B USB Bluetooth dongle ("BT 6.0", USB id `0bda:a760`) is plugged in.
- `rtl8761bu_fw.bin` and `rtl8761bu_config.bin` are placed next to
  `tools/gamepad/jetson_btrtl_8761b_fix.sh` (the script can fetch the L4T kernel
  source itself, or supply `btrtl.c` / `btrtl.h`).
- You can `ssh` into the Jetson as the user that runs the dashboard.

## Tier 1 — Driver (once per OS image / kernel)

The L4T R32.7.x kernel (4.9.337-tegra) ships a `btrtl` missing the 8761B entry,
so the dongle fails with `unknown project id 14`.

```bash
bash tools/gamepad/jetson_btrtl_8761b_fix.sh
sudo reboot
```

**Expected after reboot:** `hciconfig` lists an `hci0` adapter. Re-running the
guide moves you to Tier 2. *(Skip this tier entirely if your adapter already
shows up — e.g. a natively-supported dongle.)*

## Tier 2 — Commissioning (once per robot)

```bash
bash tools/gamepad/setup_bluetooth_host.sh
sudo reboot
```

This applies all persistent, root-level setup: ERTM disable, rfkill unblock,
`AutoEnable=true` (adapter powers on at boot), adds the dashboard user to the
`bluetooth` group, and enables `bluetooth.service`. The reboot makes the group
membership and auto-power take effect.

## Tier 3 — Verify (sign-off gate)

After the Tier 2 reboot, **without re-running setup**:

```bash
bash tools/gamepad/check_bluetooth_host.sh
```

**Pass criteria:** every check is `[PASS]` and the script exits `0` on a *cold
boot*. That proves the config survives a reboot — i.e. the robot comes up ready
on its own. A `[FAIL]` prints the exact fix (usually: re-run Tier 2).

## Tier 4 — Acceptance (prove the end-user path)

Start the dashboard and pair a controller from the **GAMEPAD** pill — no
terminal, no sudo:

```bash
python3 tools/dashboard/server.py
# then, in a browser: click the GAMEPAD pill → Continue → pick the controller
```

Hold the controller near the dongle and re-arm pairing mode (LED flashing fast)
just before picking it — pairing is the RSSI-sensitive moment; normal-range use
is fine once bonded.

---

## Sign-off

A black-mata is commissioned when, **on a clean reboot with no manual setup**:

1. `check_bluetooth_host.sh` is all `[PASS]` (exit 0), and
2. an end-user can pair a controller from the dashboard modal.

## Re-running later

- **Kernel/OS update** → redo Tier 1 (driver), then Tier 3 to confirm.
- **New robot / re-image** → Tiers 1 → 2 → 3.
- **Swapping or re-pairing controllers** → not commissioning; just use the
  dashboard pill (or `setup_gamepad.sh`) any time.

## Re-testing the procedure on a single robot

To validate the whole procedure when you only have one black-mata, reset it to a
clean baseline and run the procedure again:

```bash
bash tools/gamepad/reset_bluetooth_host.sh   # reverts Tier 2 + forgets pairings
sudo reboot
bash tools/gamepad/commission_bluetooth.sh   # walk Tiers 2 → 3 → 4 from scratch
```

`reset_bluetooth_host.sh` keeps the Tier-1 driver fix (the adapter stays
working), so you re-test the commissioning, verification, and acceptance tiers —
the parts that run per robot. Reverting Tier 1 as well is rarely needed and is
described in that script's header.

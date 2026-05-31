"""
bt_setup.py — Bluetooth pairing orchestrator for Black-Mata gamepads.

Exposes four generator functions that yield structured progress events
({'kind': ..., ...}) so the same logic can drive both:

  - this file's CLI (terminal, matches setup_gamepad.sh UX)
  - the dashboard setup modal in Phase H (SSE stream to the browser)

Run as a CLI:
    python3 tools/gamepad/bt_setup.py
"""

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

PAIRING_SCAN_SECS = 15      # initial discovery scan window
PAIR_SCAN_SECS    = 5       # re-scan inside the pair session
PAIR_WAIT_SECS    = 8       # wait after issuing `pair`
TRUST_WAIT_SECS   = 1       # wait after `trust`
CONNECT_WAIT_SECS = 5       # wait after `connect`

MODPROBE_CONF = '/etc/modprobe.d/bluetooth-xbox.conf'
ERTM_SYSFS    = '/sys/module/bluetooth/parameters/disable_ertm'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _evt(kind, **kw):
    """Build a uniform event dict."""
    return {'kind': kind, **kw}


def _run(cmd):
    """Run a command quietly, capturing stdout. Returns CompletedProcess."""
    return subprocess.run(cmd, check=False,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,
                          text=True)


def _sudo_write(path, contents):
    """Write `contents` to `path` via `sudo tee` (so we don't need to be root)."""
    p = subprocess.Popen(['sudo', 'tee', path],
                         stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    p.communicate(contents.encode())
    return p.returncode == 0


def find_hci():
    """Return the first 'hciN' adapter name from `hciconfig`, or None."""
    r = _run(['hciconfig'])
    if r.returncode != 0:
        return None
    m = re.search(r'hci\d+', r.stdout or '')
    return m.group(0) if m else None


def list_known_devices():
    """Devices already known to bluetoothctl: [{mac, name}, ...]."""
    r = _run(['bluetoothctl', 'devices'])
    devices = []
    for line in r.stdout.splitlines():
        m = re.match(r'^Device\s+([0-9A-F:]+)\s+(.+)$', line.strip())
        if m:
            devices.append({'mac': m.group(1), 'name': m.group(2)})
    return devices


# ── Phase generators ──────────────────────────────────────────────────────────

def disable_ertm():
    """Disable Bluetooth ERTM (required for Xbox-style controllers on Linux
    kernel < 5.12). Persists the modprobe option and writes the running
    kernel parameter via sysfs."""
    yield _evt('phase', label='Disable Bluetooth ERTM')

    conf = Path(MODPROBE_CONF)
    if conf.exists() and 'disable_ertm' in conf.read_text():
        yield _evt('log', message=f'Already disabled in {MODPROBE_CONF}')
    else:
        yield _evt('log', message=f'Writing {MODPROBE_CONF} (sudo)')
        if not _sudo_write(MODPROBE_CONF, 'options bluetooth disable_ertm=1\n'):
            yield _evt('error', message=f'Could not write {MODPROBE_CONF}')
            return

    if Path(ERTM_SYSFS).exists():
        _sudo_write(ERTM_SYSFS, '1')
        yield _evt('log', message='ERTM disabled in running kernel')

    yield _evt('done')


def check_adapter():
    """Restart bluetoothd, ensure a `hciN` adapter is up. The 'done' event
    carries the adapter name; 'error' if none could be brought up."""
    yield _evt('phase', label='Bluetooth adapter')

    subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'], check=False)
    time.sleep(1)
    hci = find_hci()

    if not hci:
        yield _evt('log', message='No adapter — loading btusb...')
        subprocess.run(['sudo', 'modprobe', 'btusb'], check=False)
        time.sleep(2)
        subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'], check=False)
        time.sleep(1)
        hci = find_hci()

    if not hci:
        yield _evt('error', message=(
            'No Bluetooth adapter found. Plug in the RTL8761B USB dongle. '
            'If the kernel logs "unknown project id 14", run '
            'jetson_btrtl_8761b_fix.sh first.'
        ))
        return

    subprocess.run(['sudo', 'hciconfig', hci, 'up'], check=False)
    yield _evt('done', adapter=hci)


_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_DEV_RE = re.compile(r'\bDevice\s+([0-9A-Fa-f:]{17})\s*(.*)$')


def _parse_device_line(line, devices):
    """Extract a device from one bluetoothctl output line into `devices`
    (a {mac: name} dict). Handles `[NEW] Device MAC <name>` and
    `[CHG] Device MAC Name: <name>`, while ignoring property updates like
    `RSSI: -58` or `Connected: yes`."""
    line = _ANSI_RE.sub('', line).strip()
    m = _DEV_RE.search(line)
    if not m:
        return
    mac = m.group(1).upper()
    rest = m.group(2).strip()
    prop = re.match(r'^([A-Za-z][\w-]*):\s*(.*)$', rest)
    if prop:
        # A property line. Only `Name:` carries a usable label; for anything
        # else just register that the MAC exists (name may arrive later).
        if prop.group(1) == 'Name':
            devices[mac] = prop.group(2).strip()
        else:
            devices.setdefault(mac, mac)
    else:
        # `[NEW] Device MAC <name>` — rest is the name (or a MAC-like label).
        devices[mac] = rest or devices.get(mac, mac)


def _drain_devices(proc, devices):
    """Read a scanning bluetoothctl's stdout line by line, recording devices
    as they are discovered. Returns when the stream closes (process quits)."""
    for line in proc.stdout:
        _parse_device_line(line, devices)


def scan(seconds=PAIRING_SCAN_SECS):
    """Run a Bluetooth scan for `seconds` and return discovered devices.
    Yields per-second progress events for the duration of the scan.

    Devices are captured LIVE from the scanning bluetoothctl's output stream,
    because BlueZ evicts freshly-discovered (unpaired) devices from its cache
    the moment discovery stops — so a `bluetoothctl devices` call made after
    `scan off` would miss them."""
    yield _evt('phase', label='Scanning', duration=seconds)

    devices = {}            # mac -> name, updated live by the reader thread
    proc = subprocess.Popen(
        ['bluetoothctl'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    reader = threading.Thread(target=_drain_devices, args=(proc, devices),
                              daemon=True)
    reader.start()

    proc.stdin.write('power on\nagent on\ndefault-agent\nscan on\n')
    proc.stdin.flush()

    for i in range(seconds):
        yield _evt('progress', elapsed=i, total=seconds)
        time.sleep(1)
    yield _evt('progress', elapsed=seconds, total=seconds)

    # Snapshot while discovery is still active, before the scan-stop purge.
    discovered = dict(devices)

    proc.stdin.write('scan off\nquit\n')
    proc.stdin.close()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(timeout=1)

    # Merge already-paired/known devices (which persist) with the transient
    # live-discovered ones; discovered names win for any overlap.
    merged = {d['mac']: d['name'] for d in list_known_devices()}
    merged.update(discovered)
    out = [{'mac': mac, 'name': name} for mac, name in merged.items()]
    yield _evt('done', devices=out)


def pair(mac):
    """Remove any stale entry for `mac`, then re-discover + pair + trust +
    connect in a single bluetoothctl session (so the registered agent
    stays alive and the device stays in BlueZ's cache between steps).

    Yields a continuous stream of `progress` events covering the full
    PAIR_SCAN_SECS + 1 + PAIR_WAIT_SECS + TRUST_WAIT_SECS + CONNECT_WAIT_SECS
    window, with `phase_step` events marking phase transitions."""
    yield _evt('phase', label='Pairing', mac=mac)

    info = _run(['bluetoothctl', 'info', mac]).stdout
    if 'Device' in info:
        yield _evt('log', message='Removing stale pairing for clean re-pair...')
        _run(['bluetoothctl', 'remove', mac])
        time.sleep(1)

    proc = subprocess.Popen(
        ['bluetoothctl'], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, bufsize=1,
    )

    def send(line):
        proc.stdin.write(line + '\n')
        proc.stdin.flush()

    total = (PAIR_SCAN_SECS + 1 + PAIR_WAIT_SECS
             + TRUST_WAIT_SECS + CONNECT_WAIT_SECS)
    elapsed = 0

    def tick(secs, step):
        nonlocal elapsed
        for _ in range(secs):
            yield _evt('progress', step=step, elapsed=elapsed, total=total)
            time.sleep(1)
            elapsed += 1

    send('agent on'); send('default-agent'); send('scan on')
    yield _evt('phase_step', step='Rediscovering')
    yield from tick(PAIR_SCAN_SECS, 'Rediscovering')

    send('scan off')
    yield from tick(1, 'Rediscovered')

    send(f'pair {mac}')
    yield _evt('phase_step', step='Pairing')
    yield from tick(PAIR_WAIT_SECS, 'Pairing')

    send(f'trust {mac}')
    yield _evt('phase_step', step='Trusting')
    yield from tick(TRUST_WAIT_SECS, 'Trusting')

    send(f'connect {mac}')
    yield _evt('phase_step', step='Connecting')
    yield from tick(CONNECT_WAIT_SECS, 'Connecting')

    send('quit')
    proc.stdin.close()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()

    time.sleep(1)
    if 'Connected: yes' in _run(['bluetoothctl', 'info', mac]).stdout:
        yield _evt('done', connected=True)
    else:
        yield _evt('error', message=(
            'Connection failed. Re-run the verbose shell script for full '
            'bluetoothctl output: bash tools/gamepad/setup_gamepad.sh --verbose'
        ))


def verify():
    """Confirm a gamepad evdev device is now visible."""
    yield _evt('phase', label='Verify input device')
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gamepad_core import find_gamepad
    except ImportError as e:
        yield _evt('error', message=f'evdev not available: {e}')
        return
    dev = find_gamepad()
    if dev is None:
        yield _evt('error', message='No gamepad input device detected.')
        return
    yield _evt('done', name=dev.name, path=dev.path)


# ── CLI driver ────────────────────────────────────────────────────────────────

_BAR_WIDTH = 24


def _draw_bar(filled, total, width=_BAR_WIDTH):
    if total <= 0:
        return ' ' * width
    n_fill = min(width, max(0, (filled * width) // total))
    return '█' * n_fill + '░' * (width - n_fill)


def _render(gen):
    """Drive a generator from the CLI: print events to terminal, return the
    final 'done' payload (or exit on 'error')."""
    current_step = ''
    final = None
    for ev in gen:
        k = ev['kind']
        if k == 'phase':
            print()
            print(f'── {ev["label"]} ──')
        elif k == 'log':
            print(f'  {ev["message"]}')
        elif k == 'phase_step':
            current_step = ev['step']
        elif k == 'progress':
            step = ev.get('step', current_step)
            bar  = _draw_bar(ev['elapsed'], ev['total'])
            rem  = ev['total'] - ev['elapsed']
            print(f'\r  {step:<14s} [{bar}] {rem:2d}s ',
                  end='', flush=True)
        elif k == 'done':
            print()
            final = ev
        elif k == 'error':
            print()
            print(f'  ERROR: {ev["message"]}', file=sys.stderr)
            sys.exit(1)
    return final


def main():
    print('Black-Mata gamepad setup')

    _render(disable_ertm())
    _render(check_adapter())

    print()
    print('Put the controller into Bluetooth pairing mode:')
    print('  Machenike G3 V2: hold Pair/Mode until the LED flashes rapidly.')
    print('  Xbox controller: hold the Connect button (near USB-C) ~3 s.')
    input('Press Enter when the LED is flashing... ')

    devices = _render(scan(PAIRING_SCAN_SECS))['devices']
    if not devices:
        print('No devices found. Make sure the controller is in pairing mode.')
        sys.exit(1)

    print()
    print('Devices found:')
    for i, d in enumerate(devices):
        print(f'  [{i:2d}] {d["mac"]}  {d["name"]}')
    print()
    while True:
        try:
            idx = int(input('Pick the gamepad: '))
            if 0 <= idx < len(devices):
                break
        except ValueError:
            pass
        print('Invalid selection.')

    selected = devices[idx]
    print(f'\n  Selected: {selected["name"]} ({selected["mac"]})')

    _render(pair(selected['mac']))
    _render(verify())

    print()
    print('Setup complete. Test with:')
    print('  python3 tools/gamepad/gamepad_test.py')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)

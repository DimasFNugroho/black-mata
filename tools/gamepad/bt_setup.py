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
REDISCOVER_SECS   = 25      # max wait for the controller to re-advertise inside
                            # the pair session (early-exits the moment it shows;
                            # generous so a weak beacon at range still lands)
PAIR_WAIT_SECS    = 8       # wait after issuing `pair`
TRUST_WAIT_SECS   = 1       # wait after `trust`
CONNECT_WAIT_SECS = 5       # wait after each `connect` attempt
CONNECT_ATTEMPTS  = 3       # an Xbox-style pad drops once right after `trust`,
                            # so connect may need a few tries to land + hold

# Set by the CLI --verbose flag: echo raw bluetoothctl output to stderr so the
# user can see exactly what BlueZ is doing during a failed pairing.
VERBOSE = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _evt(kind, **kw):
    """Build a uniform event dict."""
    return {'kind': kind, **kw}


def _run(cmd, timeout=10):
    """Run a command quietly, capturing stdout. Returns CompletedProcess.

    stdin is forced to /dev/null: a `bluetoothctl` one-shot like `devices` or
    `info` that inherits the terminal on stdin will drop into INTERACTIVE mode
    on BlueZ 5.48 — grabbing the TTY (raw/no-echo) and blocking until timeout,
    which then leaves the terminal broken for the next input() prompt. With
    stdin on /dev/null it sees EOF, runs the command non-interactively, exits.

    Also timeout-wrapped as a backstop: on timeout we return a
    CompletedProcess-shaped result (rc 124) with whatever was captured, so
    callers can keep using `.stdout`/`.returncode` uniformly."""
    try:
        return subprocess.run(cmd, check=False,
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        captured = e.stdout if isinstance(e.stdout, str) else ''
        return subprocess.CompletedProcess(cmd, 124, stdout=captured, stderr='')


def _btctl_script(script, timeout=10):
    """Feed `script` to bluetoothctl over stdin and return its stdout.

    The piped form is deliberate: passing commands like `power on` / `show` as
    arguments makes BlueZ 5.48's bluetoothctl drop into interactive mode and
    hang on stdin. Piping a script that ends in `quit` always terminates."""
    try:
        p = subprocess.run(['bluetoothctl'], input=script,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)
        return p.stdout or ''
    except subprocess.TimeoutExpired as e:
        return e.stdout if isinstance(e.stdout, str) else ''


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

def check_adapter():
    """No-sudo adapter readiness check, shared by the terminal flow and the
    dashboard setup modal.

    All root-level Bluetooth setup (ERTM, rfkill, adapter auto-power, the
    `bluetooth` group) is a one-time commissioning job done by
    setup_bluetooth_host.sh — NOT here. This only reads the adapter and powers
    it on via bluetoothctl (no sudo). If nothing is ready it tells the user to
    run the host setup; it deliberately cannot fix a wedged stack itself, so the
    same code works headless in the dashboard server (which has no TTY for a
    sudo password)."""
    yield _evt('phase', label='Bluetooth adapter')
    hci = find_hci()
    if not hci:
        yield _evt('error', message=(
            'No Bluetooth adapter is ready. On the robot, run '
            'tools/gamepad/setup_bluetooth_host.sh once (and '
            'jetson_btrtl_8761b_fix.sh first if using the RTL8761B dongle).'))
        return
    _btctl_script('power on\nquit\n', timeout=6)
    time.sleep(1)
    if 'Powered: yes' not in _btctl_script('show\nquit\n', timeout=6):
        yield _evt('error', message=(
            f'Adapter {hci} is not powered. Run setup_bluetooth_host.sh on the '
            'robot (it sets AutoEnable=true so the adapter powers on at boot).'))
        return
    yield _evt('log', message=f'Adapter {hci} ready.')
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
        if VERBOSE:
            sys.stderr.write(line)
        _parse_device_line(line, devices)


def scan(seconds=PAIRING_SCAN_SECS):
    """Run a Bluetooth scan for `seconds` and return discovered devices.
    Yields per-second progress events for the duration of the scan.

    Devices are captured LIVE from the scanning bluetoothctl's output stream,
    because BlueZ evicts freshly-discovered (unpaired) devices from its cache
    the moment discovery stops — so a `bluetoothctl devices` call made after
    `scan off` would miss them.

    Bonds are cleared with `remove '*'` up front, before discovery, so the
    picker shows fresh advertisements and a controller stuck in a stale bonded
    state can re-pair cleanly. (Quotes are literal — bt_shell globs `*` against
    the cwd otherwise.)"""
    yield _evt('phase', label='Scanning', duration=seconds)

    devices = {}            # mac -> name, updated live by the reader thread
    proc = subprocess.Popen(
        ['bluetoothctl'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1,
    )
    reader = threading.Thread(target=_drain_devices, args=(proc, devices),
                              daemon=True)
    reader.start()

    # Clear all bonds first, then start discovery from a clean slate.
    proc.stdin.write("power on\nagent on\ndefault-agent\nremove '*'\nscan on\n")
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
    """Pair, trust, and connect `mac` in ONE persistent bluetoothctl session,
    reading the session's output to detect success rather than guessing from
    fixed sleeps. Mirrors the sequence that works by hand:

        remove MAC -> power on -> agent on -> default-agent ->
        scan on -> (rediscover) -> scan off -> pair -> trust -> connect

    A single session is essential: BlueZ purges unpaired devices when a session
    ends, so splitting scan and pair across separate sessions makes the device
    vanish before `pair` runs — which made every attempt fail.

    We clear the cache with `remove '*'` (all devices), not `remove <MAC>`:
    removing the specific MAC left the controller unable to re-advertise into
    the same session's scan, so `pair` hit "Device not available". The quotes
    are literal — bt_shell word-expands `*` (globs the cwd) without them."""
    yield _evt('phase', label='Pairing', mac=mac)

    # One live session; a reader thread collects its output so we can watch for
    # 'Pairing successful' / 'Connection successful' as they happen.
    lines = []
    lock  = threading.Lock()
    proc  = subprocess.Popen(
        ['bluetoothctl'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1,
    )

    def _reader():
        for line in proc.stdout:
            if VERBOSE:
                sys.stderr.write(line)
            with lock:
                lines.append(line)
    threading.Thread(target=_reader, daemon=True).start()

    def send(cmd):
        proc.stdin.write(cmd + '\n')
        proc.stdin.flush()

    def mark():
        with lock:
            return len(lines)

    def seen_since(start, *needles):
        """True if any line captured since index `start` contains a needle."""
        with lock:
            return any(any(n in ln for n in needles) for ln in lines[start:])

    def rediscovered():
        """True once the target MAC re-appears AFTER discovery (re)started, so
        the stale pre-`remove '*'` line in the buffer can't trip it early."""
        with lock:
            after_scan = False
            for ln in lines:
                if 'Discovery started' in ln:
                    after_scan = True
                elif after_scan and mac in ln:
                    return True
            return False

    total = REDISCOVER_SECS + PAIR_WAIT_SECS + TRUST_WAIT_SECS + CONNECT_WAIT_SECS
    elapsed = 0

    def wait_for(step, secs, success=(), failure=(), predicate=None):
        """Tick up to `secs` seconds (1 progress event/s), stopping early when a
        success/failure marker appears AFTER this call began, or when
        `predicate()` (if given) returns True. The `yield from` caller receives
        'ok' | 'fail' | 'timeout' as the generator's value."""
        nonlocal elapsed
        start  = mark()
        result = 'timeout'
        for _ in range(secs):
            yield _evt('progress', step=step, elapsed=min(elapsed, total), total=total)
            if failure and seen_since(start, *failure):
                result = 'fail'; break
            if predicate is not None and predicate():
                result = 'ok';   break
            if success and seen_since(start, *success):
                result = 'ok';   break
            time.sleep(1)
            elapsed += 1
        return result

    def finish():
        send('exit')
        proc.stdin.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    # 1. Clear ALL cached devices, power up, register the agent, start scanning.
    #    `remove '*'` (quoted) clears everything; removing just our MAC stopped
    #    the controller from re-advertising into this scan.
    send("remove '*'")
    send('power on'); send('agent on'); send('default-agent'); send('scan on')

    # 2. Wait until the controller actually re-advertises into THIS scan, then
    #    pair the instant it shows. The beacon is intermittent and sparse at
    #    range, so a blind fixed window misses it when the pad is a metre or two
    #    away — poll for the MAC (after Discovery started) up to REDISCOVER_SECS.
    yield _evt('phase_step', step='Rediscovering')
    res = yield from wait_for('Rediscovering', REDISCOVER_SECS,
                              predicate=rediscovered)
    if res != 'ok':
        finish()
        yield _evt('error', message=(
            'Controller not found near the adapter. Move it closer to the '
            'robot, make sure it is in pairing mode (LED flashing fast), and '
            'try again.'))
        return

    # 3. Stop scanning, then pair (the device stays cached within the session).
    send('scan off')
    send(f'pair {mac}')
    yield _evt('phase_step', step='Pairing')
    res = yield from wait_for('Pairing', PAIR_WAIT_SECS,
                              success=('Pairing successful', 'Paired: yes'),
                              failure=('Failed to pair',))
    if res != 'ok':
        finish()
        yield _evt('error', message=(
            'Pairing did not complete. Make sure the controller is in pairing '
            'mode (LED flashing fast); re-run with --verbose to see why.'))
        return

    # 4. Trust it so BlueZ auto-reconnects on future boots.
    send(f'trust {mac}')
    yield _evt('phase_step', step='Trusting')
    yield from wait_for('Trusting', TRUST_WAIT_SECS,
                        success=('trust succeeded', 'Trusted: yes'))

    # 5. Connect. An Xbox pad drops right after `trust`, so retry within the
    #    SAME session until the connection holds.
    yield _evt('phase_step', step='Connecting')
    connected = False
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        send(f'connect {mac}')
        res = yield from wait_for('Connecting', CONNECT_WAIT_SECS,
                                  success=('Connection successful',),
                                  failure=('Failed to connect',))
        if res == 'ok':
            connected = True
            break
        yield _evt('log', message=f'Connect attempt {attempt}/{CONNECT_ATTEMPTS} '
                                  'did not hold, retrying...')

    finish()

    if connected:
        yield _evt('done', connected=True)
    else:
        yield _evt('error', message=(
            'Paired but could not hold a connection. Wake the controller and '
            're-run; --verbose shows the bluetoothctl output.'))


def verify():
    """Confirm a gamepad evdev device is now visible."""
    yield _evt('phase', label='Verify input device')
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gamepad_core import find_gamepad, dev_path
    except ImportError as e:
        yield _evt('error', message=f'evdev not available: {e}')
        return
    dev = find_gamepad()
    if dev is None:
        yield _evt('error', message='No gamepad input device detected.')
        return
    # dev_path() handles evdev 0.7.0 (.fn, the Jetson) vs >= 1.0 (.path).
    yield _evt('done', name=dev.name, path=dev_path(dev))


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
    global VERBOSE
    args = sys.argv[1:]
    VERBOSE = '--verbose' in args or '-v' in args

    print('Black-Mata gamepad setup')

    # Root-level setup (ERTM, rfkill, auto-power) is a one-time job done by
    # setup_bluetooth_host.sh — this flow is sudo-free.
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

    # Xbox-style pads drop out of pairing mode in ~20-30 s. If the user dithered
    # over the device list, the LED may have stopped flashing — give them a
    # chance to re-arm pairing mode right before we pair.
    print('\n  Make sure the controller is STILL in pairing mode (LED flashing).')
    input('  Press Enter to pair... ')

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

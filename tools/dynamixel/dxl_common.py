"""
dxl_common.py

Shared helpers for Dynamixel Python tools.

Communicates with an OpenCM9.04 running dxl_commander.ino via text
commands over USB Serial. No DynamixelSDK required.

Usage:
    from dxl_common import open_port, send_cmd, read_until_ok
"""

import sys
import glob
import time
import argparse
import serial


# ── Default connection settings ────────────────────────────────────────────────
USB_BAUD = 115200


# ── Port helpers ──────────────────────────────────────────────────────────────

def auto_detect_port():
    """Find the OpenCM serial port on ARM."""
    candidates = (
        glob.glob("/dev/opencm")
        + glob.glob("/dev/serial/by-id/*ROBOTIS*")
        + sorted(glob.glob("/dev/ttyACM*"))
    )
    return candidates[0] if candidates else None


def open_port(port=None, baud=USB_BAUD):
    """Open serial connection to the OpenCM. Returns a pyserial instance."""
    if port is None:
        port = auto_detect_port()
    if port is None:
        print("ERROR: Could not find OpenCM serial port.", file=sys.stderr)
        print("  Try: ls /dev/opencm /dev/serial/by-id/ /dev/ttyACM*", file=sys.stderr)
        sys.exit(1)

    try:
        ser = serial.Serial(port, baud, timeout=2)
    except serial.SerialException as e:
        print("ERROR: Could not open port {}: {}".format(port, e), file=sys.stderr)
        sys.exit(1)

    # Wait for the OpenCM to print its ready banner
    time.sleep(1.5)
    ser.reset_input_buffer()

    print("# Port: {}  Baud: {}".format(port, baud))
    return ser


def send_cmd(ser, cmd):
    """Send a command to the OpenCM."""
    ser.reset_input_buffer()
    ser.write((cmd.strip() + "\n").encode())
    ser.flush()


def read_line(ser, timeout=5.0):
    """Read one line from the OpenCM. Returns None on timeout."""
    ser.timeout = timeout
    line = ser.readline()
    if not line:
        return None
    return line.decode("utf-8", errors="replace").strip()


def read_until_ok(ser, prefix="OK", timeout=30.0):
    """Read lines until one starts with prefix or ERR. Returns (lines, ok_line)."""
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = read_line(ser, timeout=max(0.5, deadline - time.time()))
        if line is None:
            continue
        if line.startswith("#"):
            continue
        if line.startswith(prefix) or line.startswith("ERR"):
            return lines, line
        lines.append(line)
    return lines, None


# ── Shared CLI argument ──────────────────────────────────────────────────────

def port_arg(description=""):
    """Return an ArgumentParser with --port and --baud flags."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--port", "-p", default=None,
                   help="Serial port (default: auto-detect)")
    p.add_argument("--baud", "-b", type=int, default=USB_BAUD,
                   help="USB baud rate (default: {})".format(USB_BAUD))
    return p

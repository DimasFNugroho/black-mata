"""
dxl_common.py

Shared configuration, AX-12A control table addresses, and connection
helpers for the Python DynamixelSDK tools.

The tools connect to a Dynamixel bus via an OpenCM9.04 running
dxl_u2d2_bridge.ino (transparent USB-to-Dynamixel passthrough).

Usage:
    from dxl_common import open_port, PROTOCOL, BAUD_RATE
"""

import sys
import argparse
from typing import Optional, Tuple
from dynamixel_sdk import PortHandler, PacketHandler

# ── Default connection settings ────────────────────────────────────────────────
DEFAULT_PORT      = "/dev/ttyACM0"   # adjust for your OS (/dev/ttyUSB0, COM3, …)
BAUD_RATE         = 1_000_000
PROTOCOL          = 1.0             # AX-12A uses Protocol 1.0

# ── AX-12A control table (Protocol 1.0) ───────────────────────────────────────
ADDR_ID                  = 3
ADDR_CW_ANGLE_LIMIT      = 6   # 2 bytes
ADDR_CCW_ANGLE_LIMIT     = 8   # 2 bytes
ADDR_TORQUE_ENABLE       = 24
ADDR_GOAL_POSITION       = 30  # 2 bytes
ADDR_MOVING_SPEED        = 32  # 2 bytes
ADDR_PRESENT_POSITION    = 36  # 2 bytes
ADDR_PRESENT_SPEED       = 38  # 2 bytes
ADDR_PRESENT_LOAD        = 40  # 2 bytes
ADDR_PRESENT_VOLTAGE     = 42  # 1 byte
ADDR_PRESENT_TEMPERATURE = 43  # 1 byte
ADDR_MOVING              = 46  # 1 byte

# ── AX-12A limits ─────────────────────────────────────────────────────────────
AX12A_MIN_TICKS = 0
AX12A_MAX_TICKS = 1023
AX12A_MAX_DEG   = 300.0

# ── Conversions ───────────────────────────────────────────────────────────────

def ticks_to_deg(ticks: int) -> float:
    return ticks * AX12A_MAX_DEG / AX12A_MAX_TICKS

def deg_to_ticks(deg: float) -> int:
    return int(round(deg * AX12A_MAX_TICKS / AX12A_MAX_DEG))

def ticks_to_rpm(ticks: int) -> float:
    """AX-12A speed: 1 tick ≈ 0.111 RPM"""
    return (ticks & 0x3FF) * 0.111

def ticks_to_load_pct(ticks: int) -> float:
    """AX-12A load: bits 0-9 are magnitude, bit 10 is direction"""
    return (ticks & 0x3FF) * 100.0 / 1023.0

def is_wheel_mode(cw: int, ccw: int) -> bool:
    return cw == 0 and ccw == 0

# ── Port helpers ───────────────────────────────────────────────────────────────

def open_port(port: str, baud: int = BAUD_RATE) -> Tuple[PortHandler, PacketHandler]:
    """Open port and return (port_handler, packet_handler). Exits on failure."""
    ph = PortHandler(port)
    pkt = PacketHandler(PROTOCOL)
    if not ph.openPort():
        print(f"ERROR: could not open port {port}", file=sys.stderr)
        sys.exit(1)
    if not ph.setBaudRate(baud):
        print(f"ERROR: could not set baud rate {baud}", file=sys.stderr)
        sys.exit(1)
    return ph, pkt


def read1(pkt: PacketHandler, ph: PortHandler, servo_id: int, addr: int) -> Optional[int]:
    val, result, error = pkt.read1ByteTxRx(ph, servo_id, addr)
    if result != 0 or error != 0:
        return None
    return val


def read2(pkt: PacketHandler, ph: PortHandler, servo_id: int, addr: int) -> Optional[int]:
    val, result, error = pkt.read2ByteTxRx(ph, servo_id, addr)
    if result != 0 or error != 0:
        return None
    return val


def write1(pkt: PacketHandler, ph: PortHandler, servo_id: int, addr: int, value: int) -> bool:
    result, error = pkt.write1ByteTxRx(ph, servo_id, addr, value)
    return result == 0 and error == 0


def write2(pkt: PacketHandler, ph: PortHandler, servo_id: int, addr: int, value: int) -> bool:
    result, error = pkt.write2ByteTxRx(ph, servo_id, addr, value)
    return result == 0 and error == 0


# ── Shared CLI argument ────────────────────────────────────────────────────────

def port_arg(description: str = "") -> argparse.ArgumentParser:
    """Return an ArgumentParser pre-loaded with --port and --baud flags."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--port", "-p", default=DEFAULT_PORT,
                   help=f"Serial port (default: {DEFAULT_PORT})")
    p.add_argument("--baud", "-b", type=int, default=BAUD_RATE,
                   help=f"Baud rate (default: {BAUD_RATE})")
    return p

#!/usr/bin/env python3
"""
serial_rw_example.py — Jetson-side HEARTBEAT sender.

Sends a HEARTBEAT packet to the OpenCM9.04 at a fixed interval and waits for
an HB_ACK callback. Prints "Healthy" when a valid ACK is received.

Packet layout (9 bytes, fixed length):
  [0]    START   = 0xAA
  [1]    CMD     uint8  (0x01 = HEARTBEAT, 0x02 = HB_ACK)
  [2]    SEQ     uint8  (rolling 0-255)
  [3..6] PAYLOAD uint8[4]  (reserved, zeros)
  [7]    CRC_HI  uint8
  [8]    CRC_LO  uint8

CRC-16 CCITT (poly=0x1021, init=0xFFFF) computed over bytes [0..6].

Usage:
    python scripts/serial_rw_example.py --port /dev/ttyACM0 [--baud 115200] [--interval 2.0]
"""

import argparse
import time
import serial

# ── Constants ──────────────────────────────────────────────────────────────────

START_BYTE    = 0xAA
CMD_HEARTBEAT = 0x01
CMD_HB_ACK    = 0x02
PACKET_SIZE   = 9
PAYLOAD_SIZE  = 4
READ_TIMEOUT  = 1.0   # seconds to wait for a response

# ── CRC-16 CCITT (poly=0x1021, init=0xFFFF) ───────────────────────────────────

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        crc &= 0xFFFF
    return crc

# ── Packet helpers ─────────────────────────────────────────────────────────────

def build_packet(cmd: int, seq: int) -> bytes:
    """Build a 9-byte packet with CRC appended."""
    payload = bytes(PAYLOAD_SIZE)   # all zeros
    body = bytes([START_BYTE, cmd, seq & 0xFF]) + payload   # bytes [0..6]
    crc  = crc16(body)
    return body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])

def parse_packet(raw: bytes):
    """
    Validate and parse a raw 9-byte packet.
    Returns a dict on success, or None if the packet is invalid.
    """
    if len(raw) != PACKET_SIZE:
        return None
    if raw[0] != START_BYTE:
        return None

    expected = crc16(raw[:PACKET_SIZE - 2])
    received = (raw[7] << 8) | raw[8]
    if expected != received:
        return None

    return {
        'cmd':     raw[1],
        'seq':     raw[2],
        'payload': raw[3:7],
        'crc':     received,
    }

def cmd_name(cmd: int) -> str:
    return {CMD_HEARTBEAT: 'HEARTBEAT', CMD_HB_ACK: 'HB_ACK'}.get(cmd, f'0x{cmd:02X}')

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Jetson HEARTBEAT sender')
    parser.add_argument('--port',     required=True,              help='Serial port (e.g. /dev/ttyACM0)')
    parser.add_argument('--baud',     type=int,   default=115200, help='Baud rate')
    parser.add_argument('--interval', type=float, default=2.0,    help='Heartbeat interval in seconds')
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=READ_TIMEOUT)
    print(f"Connected : {args.port} @ {args.baud} baud")
    print(f"Interval  : {args.interval}s\n")

    seq = 0
    try:
        while True:
            # ── Transmit HEARTBEAT ─────────────────────────────────────────────
            pkt = build_packet(CMD_HEARTBEAT, seq)
            ser.write(pkt)
            print(f"[TX] {cmd_name(CMD_HEARTBEAT):<12} seq={seq:3d}  raw={pkt.hex(' ')}")

            # ── Wait for HB_ACK ────────────────────────────────────────────────
            raw = ser.read(PACKET_SIZE)

            if len(raw) < PACKET_SIZE:
                print(f"[RX] Timeout — no response (got {len(raw)} bytes)\n")
            else:
                parsed = parse_packet(raw)
                if parsed is None:
                    print(f"[RX] Bad packet (CRC error or wrong START)  raw={raw.hex(' ')}\n")
                elif parsed['cmd'] != CMD_HB_ACK:
                    print(f"[RX] Unexpected cmd={cmd_name(parsed['cmd'])}\n")
                else:
                    print(f"[RX] {cmd_name(parsed['cmd']):<12} seq={parsed['seq']:3d}  raw={raw.hex(' ')}")
                    print(f"     Status : Healthy\n")

            seq = (seq + 1) & 0xFF
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ser.close()

if __name__ == '__main__':
    main()

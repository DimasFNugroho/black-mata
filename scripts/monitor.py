#!/usr/bin/env python3
"""
monitor.py — Simple serial monitor for black_mata_firmware.

Usage:
    python scripts/monitor.py --port /dev/ttyACM0 [--baud 115200]
"""

import argparse
import serial


class SerialMonitor:
    def __init__(self, port: str, baud: int = 115200):
        self._port = port
        self._baud = baud
        self._conn: serial.Serial | None = None

    def open(self):
        self._conn = serial.Serial(self._port, self._baud, timeout=1)
        print(f"Connected to {self._port} @ {self._baud} baud. Press Ctrl+C to exit.\n")

    def close(self):
        if self._conn and self._conn.is_open:
            self._conn.close()

    def run(self):
        self.open()
        try:
            while True:
                line = self._conn.readline()
                if line:
                    print(line.decode(errors="replace").rstrip())
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
        finally:
            self.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serial monitor for black_mata_firmware")
    parser.add_argument("--port", required=True, help="Serial port (e.g. /dev/ttyACM0)")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    SerialMonitor(args.port, args.baud).run()

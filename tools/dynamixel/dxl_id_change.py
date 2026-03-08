#!/usr/bin/env python3
"""
dxl_id_change.py

Changes a Dynamixel servo's ID. Validates the new ID, checks for
conflicts, disables torque, writes EEPROM, and verifies the change.

WARNING: ID is stored in EEPROM. Do not power off during the write.
         Valid IDs: 1–252. ID 254 is the broadcast ID (reserved).

Usage:
    python3 dxl_id_change.py --current 1 --new 5
    python3 dxl_id_change.py --current 1 --new 5 --port /dev/ttyACM0

Requires the OpenCM9.04 to be running dxl_u2d2_bridge.ino.
"""

import sys
import time
from dynamixel_sdk import COMM_SUCCESS
from dxl_common import open_port, ADDR_ID, ADDR_TORQUE_ENABLE, write1, port_arg


def main():
    p = port_arg("Change a Dynamixel servo ID")
    p.add_argument("--current", "-c", type=int, required=True, help="Current servo ID")
    p.add_argument("--new",     "-n", type=int, required=True, help="New servo ID to assign")
    args = p.parse_args()

    current_id = args.current
    new_id     = args.new

    print("==============================================")
    print(" Dynamixel ID Change (Python / DynamixelSDK)")
    print("==============================================")
    print(f"Current ID : {current_id}")
    print(f"New ID     : {new_id}")
    print(f"Port       : {args.port}")
    print(f"Baud rate  : {args.baud}")
    print()

    # Validate
    if not (1 <= new_id <= 252):
        print("ERROR: NEW_ID must be between 1 and 252.", file=sys.stderr)
        sys.exit(1)
    if current_id == new_id:
        print("ERROR: current ID and new ID are the same.", file=sys.stderr)
        sys.exit(1)

    ph, pkt = open_port(args.port, args.baud)

    # Ping current ID
    print(f"Pinging ID {current_id}... ", end="", flush=True)
    model, result, _ = pkt.ping(ph, current_id)
    if result != COMM_SUCCESS:
        print("FAILED. Servo not found. Check wiring and ID.")
        ph.closePort()
        sys.exit(1)
    print(f"OK (model {model})")

    # Check new ID is free
    print(f"Checking ID {new_id} is free... ", end="", flush=True)
    _, result2, _ = pkt.ping(ph, new_id)
    if result2 == COMM_SUCCESS:
        print("CONFLICT. A servo with that ID already exists.")
        ph.closePort()
        sys.exit(1)
    print("OK")

    # Disable torque before writing EEPROM
    write1(pkt, ph, current_id, ADDR_TORQUE_ENABLE, 0)
    time.sleep(0.05)

    # Write new ID
    print(f"Writing new ID {new_id}... ", end="", flush=True)
    result3, error3 = pkt.write1ByteTxRx(ph, current_id, ADDR_ID, new_id)
    if result3 != COMM_SUCCESS or error3 != 0:
        print(f"FAILED (result={result3}, error={error3}).")
        ph.closePort()
        sys.exit(1)
    print("OK")

    time.sleep(0.3)

    # Verify
    print(f"Verifying new ID {new_id}... ", end="", flush=True)
    _, result4, _ = pkt.ping(ph, new_id)
    if result4 == COMM_SUCCESS:
        print(f"SUCCESS. Servo now responds at ID {new_id}.")
    else:
        print("FAILED. Servo did not respond at new ID.")
        ph.closePort()
        sys.exit(1)

    ph.closePort()


if __name__ == "__main__":
    main()

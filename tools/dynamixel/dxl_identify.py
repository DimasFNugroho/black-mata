#!/usr/bin/env python3
"""
dxl_identify.py

Identifies all Dynamixel servos by nudging them one at a time and asking
the user which physical joint each one corresponds to.

Saves the result to servo_map.json in the current directory.

Usage:
    python3 dxl_identify.py
    python3 dxl_identify.py --port /dev/ttyACM0
    python3 dxl_identify.py --port /dev/ttyACM0 --output my_map.json
    python3 dxl_identify.py --nudge 15 --speed 150
"""

import json
import sys
import time
from dxl_common import open_port, send_cmd, read_until_ok, read_line, port_arg

NUDGE_DEG_DEFAULT = 10.0
SPEED_DEFAULT = 150

JOINT_LABELS = [
    "FL_steering",
    "FL_wheel",
    "FR_steering",
    "FR_wheel",
    "RL_steering",
    "RL_wheel",
    "RR_steering",
    "RR_wheel",
]


def scan_servos(ser):
    """Return list of dicts: {id, model, fw, mode}"""
    print("\nScanning for servos...")
    send_cmd(ser, "SCAN 20")
    lines, ok_line = read_until_ok(ser, prefix="OK,SCAN", timeout=60)

    servos = []
    for line in lines:
        if line.startswith("FOUND,"):
            parts = line.split(",")
            if len(parts) >= 5:
                servos.append({
                    "id":    int(parts[1]),
                    "model": parts[2],
                    "fw":    parts[3],
                    "mode":  parts[4].strip(),
                })

    if not servos:
        print("ERROR: No servos found. Check wiring and firmware.", file=sys.stderr)
        sys.exit(1)

    print("Found {} servo(s): IDs {}".format(
        len(servos), [s["id"] for s in servos]))
    return servos


def get_mode(ser, servo_id):
    """Return current mode string: 'JOINT' or 'WHEEL'."""
    send_cmd(ser, "GETMODE {}".format(servo_id))
    line = read_line(ser, timeout=5.0)
    if line and line.startswith("OK,GETMODE"):
        parts = line.split(",")
        if len(parts) >= 4:
            return parts[3].strip()
    return None


def set_mode(ser, servo_id, mode):
    """Set mode to 'JOINT' or 'WHEEL'. Returns True on success."""
    send_cmd(ser, "SETMODE {} {}".format(servo_id, mode))
    line = read_line(ser, timeout=5.0)
    return line is not None and line.startswith("OK,SETMODE")


def nudge_servo(ser, servo_id, nudge_deg, speed):
    """Nudge servo and wait for completion. Returns True on success."""
    send_cmd(ser, "NUDGE {} {} {}".format(servo_id, nudge_deg, speed))
    _, ok_line = read_until_ok(ser, prefix="OK,NUDGE", timeout=15)
    return ok_line is not None and ok_line.startswith("OK")


def identify_servo(ser, servo, nudge_deg, speed):
    """
    Nudge a single servo (handling mode switching) and ask the user to
    identify it. Returns the label string entered by the user.
    """
    servo_id = servo["id"]
    original_mode = servo["mode"]

    print("\n--- Servo ID {} (model: {}, current mode: {}) ---".format(
        servo_id, servo["model"], original_mode))

    # Switch to JOINT mode if needed
    switched = False
    if original_mode == "WHEEL":
        print("  Switching to JOINT mode for nudge...")
        if not set_mode(ser, servo_id, "JOINT"):
            print("  WARNING: Could not switch mode. Skipping nudge.", file=sys.stderr)
            label = input("  Enter label for this servo (or press Enter to skip): ").strip()
            return label or None

        switched = True
        time.sleep(0.3)

    # Nudge
    print("  Nudging servo {}...".format(servo_id))
    success = nudge_servo(ser, servo_id, nudge_deg, speed)
    if not success:
        print("  WARNING: Nudge command did not complete successfully.", file=sys.stderr)

    # Restore original mode
    if switched:
        print("  Restoring WHEEL mode...")
        set_mode(ser, servo_id, "WHEEL")
        time.sleep(0.3)

    # Ask user
    print()
    print("  Which joint just moved?")
    print("  Suggestions: {}".format(", ".join(JOINT_LABELS)))
    print("  (or type any custom label, or press Enter to skip)")
    label = input("  > ").strip()
    return label or None


def main():
    p = port_arg("Identify Dynamixel servos by nudging them one at a time")
    p.add_argument("--output", "-o", default="servo_map.json",
                   help="Output file path (default: servo_map.json)")
    p.add_argument("--nudge", "-n", type=float, default=NUDGE_DEG_DEFAULT,
                   help="Degrees to nudge each servo (default: {})".format(NUDGE_DEG_DEFAULT))
    p.add_argument("--speed", "-s", type=int, default=SPEED_DEFAULT,
                   help="Nudge speed in ticks 1-1023 (default: {})".format(SPEED_DEFAULT))
    args = p.parse_args()

    ser = open_port(args.port, args.baud)

    print("==============================================")
    print(" Dynamixel Servo Identifier")
    print(" Nudge: {} deg  Speed: {}".format(args.nudge, args.speed))
    print(" Output: {}".format(args.output))
    print("==============================================")

    servos = scan_servos(ser)

    servo_map = {}
    skipped = []

    try:
        for servo in servos:
            label = identify_servo(ser, servo, args.nudge, args.speed)
            if label:
                servo_map[str(servo["id"])] = {
                    "label": label,
                    "model": servo["model"],
                    "mode":  servo["mode"],
                }
                print("  Saved: ID {} → {}".format(servo["id"], label))
            else:
                skipped.append(servo["id"])
                print("  Skipped ID {}.".format(servo["id"]))

    except KeyboardInterrupt:
        print("\n\nInterrupted. Saving partial results...")

    finally:
        ser.close()

    if not servo_map:
        print("\nNo mappings recorded. Nothing saved.")
        sys.exit(0)

    with open(args.output, "w") as f:
        json.dump(servo_map, f, indent=2)

    print("\n==============================================")
    print(" Servo Map")
    print("==============================================")
    for sid, info in servo_map.items():
        print("  ID {:>3s} → {}".format(sid, info["label"]))
    if skipped:
        print("  Skipped IDs: {}".format(skipped))
    print("\nSaved to: {}".format(args.output))


if __name__ == "__main__":
    main()

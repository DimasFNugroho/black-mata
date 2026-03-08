#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FIRMWARE_DIR = BASE_DIR / "firmware"
FQBN = "OpenCM904:OpenCM904:OpenCM904"
ARDUINO_CLI = BASE_DIR / "bin" / "arduino-cli"


def scan_sketches(firmware_dir: Path) -> list[Path]:
    sketches = []
    if not firmware_dir.exists():
        return sketches

    for ino in firmware_dir.glob("*/*.ino"):
        if ino.is_file():
            sketches.append(ino)

    sketches.sort()
    return sketches


def choose_item(title: str, items: list[str]) -> int:
    print(f"\n{title}")
    for i, item in enumerate(items, start=1):
        print(f"  {i}. {item}")

    while True:
        try:
            choice = input(f"Enter number [1-{len(items)}]: ").strip()
        except KeyboardInterrupt:
            print("\nCancelled.")
            raise SystemExit(0)
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        print("Invalid selection.")


def validate_sketch_name(sketch_ino: Path) -> tuple[bool, str]:
    sketch_dir = sketch_ino.parent.name
    ino_name = sketch_ino.stem
    if sketch_dir != ino_name:
        return False, (
            f'Sketch folder "{sketch_dir}" and file "{sketch_ino.name}" do not match. '
            "Arduino works best when they have the same name."
        )
    return True, ""


def compile_sketch(sketch_dir: Path, fqbn: str) -> int:
    cmd = [
        str(ARDUINO_CLI),
        "compile",
        "--fqbn",
        fqbn,
        "--export-binaries",
        str(sketch_dir),
    ]

    print("\nRunning command:")
    print("  " + " ".join(cmd))
    print()

    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile OpenCM9.04 Arduino sketches.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Compile all sketches non-interactively.")
    group.add_argument("--sketch", metavar="NAME", help="Compile a specific sketch by name.")
    args = parser.parse_args()

    if not ARDUINO_CLI.is_file():
        print(f"arduino-cli not found: {ARDUINO_CLI}")
        return 1

    print("Scanning sketches under:")
    print(f"  {FIRMWARE_DIR}")

    ino_files = scan_sketches(FIRMWARE_DIR)

    if not ino_files:
        print("\nNo sketches found.")
        print("Expected layout:")
        print("  firmware/<sketch_name>/<sketch_name>.ino")
        return 1

    # Non-interactive: compile all sketches
    if args.all:
        failed = []
        for ino in ino_files:
            print(f"\n--- Compiling {ino.parent.name} ---")
            rc = compile_sketch(ino.parent, FQBN)
            if rc != 0:
                failed.append(ino.parent.name)
        if failed:
            print(f"\nFailed sketches: {', '.join(failed)}")
            return 1
        print("\nAll sketches compiled successfully.")
        return 0

    # Non-interactive: compile a specific sketch by name
    if args.sketch:
        match = [ino for ino in ino_files if ino.parent.name == args.sketch]
        if not match:
            names = [ino.parent.name for ino in ino_files]
            print(f"Sketch '{args.sketch}' not found. Available: {', '.join(names)}")
            return 1
        return compile_sketch(match[0].parent, FQBN)

    # Interactive mode
    display_items = []
    for ino in ino_files:
        sketch_name = ino.parent.name
        rel_path = ino.relative_to(BASE_DIR)
        display_items.append(f"{sketch_name} -> {rel_path}")

    selected_index = choose_item("Available sketches:", display_items)
    selected_ino = ino_files[selected_index]
    selected_sketch_dir = selected_ino.parent
    selected_sketch_name = selected_sketch_dir.name

    print("\nSelected sketch:")
    print(f"  {selected_sketch_name}")
    print(f"  {selected_ino.relative_to(BASE_DIR)}")

    ok, warning = validate_sketch_name(selected_ino)
    if not ok:
        print(f"\nWarning: {warning}")
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            print("Cancelled.")
            return 0

    rc = compile_sketch(selected_sketch_dir, FQBN)
    if rc != 0:
        print("\nBuild failed.")
        return rc

    print("\nBuild finished.")

    build_dir = selected_sketch_dir / "build"
    if build_dir.exists():
        print("Build folder:")
        print(f"  {build_dir.relative_to(BASE_DIR)}")

    produced = []
    for ext in (".bin", ".hex"):
        produced.extend(selected_sketch_dir.rglob(f"*{ext}"))

    if produced:
        print("\nDetected artifacts:")
        for p in sorted(produced):
            try:
                rel = p.relative_to(BASE_DIR)
            except ValueError:
                rel = p
            print(f"  - {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

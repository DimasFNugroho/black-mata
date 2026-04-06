# black_mata_firmware — LED Blink Template

FreeRTOS OOP template for the OpenCM9.04. Blinks the built-in LED at 1 Hz to confirm the scheduler and static task allocation are working.

---

## Prerequisites

**arduino-cli** installed and the OpenCM9.04 board package added.

**Libraries** (install once):
```bash
arduino-cli lib install "STM32FreeRTOS"
```

---

## Upload

From the repo root:
```bash
python build.py
```

Or manually:
```bash
arduino-cli compile --fqbn robotis:stm32f1:OpenCM904 firmware/black_mata_firmware
arduino-cli upload  --fqbn robotis:stm32f1:OpenCM904 --port /dev/ttyACM0 firmware/black_mata_firmware
```

Replace `/dev/ttyACM0` with your actual port (`arduino-cli board list` to find it).

---

## Test

**Visual:** the built-in LED should blink at 1 Hz (0.5 s on, 0.5 s off).

**Serial (optional):** run the monitor to catch any startup errors:
```bash
python scripts/monitor.py --port /dev/ttyACM0
```

Expected output on success — silence (no error messages printed).  
On failure — `ERROR: LedBlinker task creation failed` will appear.

Press `Ctrl+C` to exit the monitor.

---

## File Map

| File | Purpose |
|---|---|
| `black_mata_firmware.ino` | Entry point — instantiates objects, starts scheduler |
| `TaskConfig.h` | Task priorities and stack sizes |
| `LedBlinker.h/.cpp` | FreeRTOS blink task wrapped in a class |

/**
 * dxl_id_scan.ino
 *
 * Scans the Dynamixel bus for all connected servos.
 * Tries common baud rates automatically.
 * Reports each found servo's ID, model number, and firmware version.
 *
 * No configuration needed — just flash and monitor.
 *
 * Wiring:
 *   OpenCM9.04 3-pin TTL Dynamixel connector → servo chain
 *
 * Output: 115200 baud USB Serial.
 */

#include <Dynamixel2Arduino.h>

#define DEBUG_SERIAL  Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28

// Baud rates to scan (most common AX-12A rates)
const uint32_t BAUD_RATES[] = { 1000000, 57600, 115200, 19200, 9600 };
const uint8_t  NUM_BAUDS    = sizeof(BAUD_RATES) / sizeof(BAUD_RATES[0]);

// Protocol 1.0 for AX-12A (set to 2.0 for XM/XH series)
const float DXL_PROTOCOL = 1.0;

Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);

void scanAtBaud(uint32_t baud) {
  dxl.begin(baud);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  DEBUG_SERIAL.print("\n  Baud rate: ");
  DEBUG_SERIAL.println(baud);

  uint8_t found = 0;

  for (uint8_t id = 1; id < 253; id++) {
    if (dxl.ping(id)) {
      found++;
      DEBUG_SERIAL.print("    [FOUND] ID: ");
      DEBUG_SERIAL.print(id);
      DEBUG_SERIAL.print("  Model: ");
      DEBUG_SERIAL.print(dxl.getModelNumber(id));
      DEBUG_SERIAL.print("  FW ver: ");
      DEBUG_SERIAL.println(dxl.readControlTableItem(ControlTableItem::FIRMWARE_VERSION, id));
    }
  }

  if (found == 0) {
    DEBUG_SERIAL.println("    No servos found at this baud rate.");
  } else {
    DEBUG_SERIAL.print("    Total found: ");
    DEBUG_SERIAL.println(found);
  }
}

void setup() {
  DEBUG_SERIAL.begin(115200);
  while (!DEBUG_SERIAL) delay(10);

  DEBUG_SERIAL.println("==============================================");
  DEBUG_SERIAL.println(" Dynamixel ID Scanner");
  DEBUG_SERIAL.println(" Protocol 1.0 (AX series)");
  DEBUG_SERIAL.println("==============================================");

  for (uint8_t i = 0; i < NUM_BAUDS; i++) {
    scanAtBaud(BAUD_RATES[i]);
  }

  DEBUG_SERIAL.println("\nScan complete.");
  DEBUG_SERIAL.println("Rescanning in 10 seconds...");
}

void loop() {
  delay(10000);

  DEBUG_SERIAL.println("\n----------------------------------------------");
  DEBUG_SERIAL.println("Rescanning...");

  for (uint8_t i = 0; i < NUM_BAUDS; i++) {
    scanAtBaud(BAUD_RATES[i]);
  }

  DEBUG_SERIAL.println("\nScan complete. Next scan in 10 seconds...");
}

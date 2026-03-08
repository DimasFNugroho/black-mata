/**
 * dxl_id_change.ino
 *
 * Changes a Dynamixel servo's ID from CURRENT_ID to NEW_ID.
 * Verifies the change by pinging the new ID after writing.
 *
 * WARNING: ID is stored in EEPROM. Do not power off during the write.
 *          Valid IDs: 1–252. ID 254 is the broadcast ID (reserved).
 *
 * Configuration:
 *   Set CURRENT_ID and NEW_ID below, then compile and flash.
 *
 * Output: 115200 baud USB Serial.
 */

#include <Dynamixel2Arduino.h>

// ── User configuration ────────────────────────────────────────────────────────
#define CURRENT_ID   1      // ID the servo currently has
#define NEW_ID       2      // ID to assign
#define BAUD_RATE    1000000
// ─────────────────────────────────────────────────────────────────────────────

#define DEBUG_SERIAL  Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28
#define DXL_PROTOCOL  1.0f

Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);

void setup() {
  DEBUG_SERIAL.begin(115200);
  while (!DEBUG_SERIAL) delay(10);

  dxl.begin(BAUD_RATE);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  DEBUG_SERIAL.println("==============================================");
  DEBUG_SERIAL.println(" Dynamixel ID Change");
  DEBUG_SERIAL.println("==============================================");
  DEBUG_SERIAL.print("Current ID : "); DEBUG_SERIAL.println(CURRENT_ID);
  DEBUG_SERIAL.print("New ID     : "); DEBUG_SERIAL.println(NEW_ID);
  DEBUG_SERIAL.print("Baud rate  : "); DEBUG_SERIAL.println(BAUD_RATE);
  DEBUG_SERIAL.println();

  // Validate
  if (NEW_ID < 1 || NEW_ID > 252) {
    DEBUG_SERIAL.println("ERROR: NEW_ID must be between 1 and 252.");
    return;
  }
  if (CURRENT_ID == NEW_ID) {
    DEBUG_SERIAL.println("ERROR: CURRENT_ID and NEW_ID are the same.");
    return;
  }

  // Check servo is reachable
  DEBUG_SERIAL.print("Pinging ID ");
  DEBUG_SERIAL.print(CURRENT_ID);
  DEBUG_SERIAL.print("... ");

  if (!dxl.ping(CURRENT_ID)) {
    DEBUG_SERIAL.println("FAILED. Servo not found. Check wiring and ID.");
    return;
  }
  DEBUG_SERIAL.println("OK");

  // Check new ID is not already taken
  DEBUG_SERIAL.print("Checking ID ");
  DEBUG_SERIAL.print(NEW_ID);
  DEBUG_SERIAL.print(" is free... ");

  if (dxl.ping(NEW_ID)) {
    DEBUG_SERIAL.println("CONFLICT. A servo with that ID already exists.");
    return;
  }
  DEBUG_SERIAL.println("OK");

  // Disable torque before writing EEPROM
  dxl.torqueOff(CURRENT_ID);

  // Write new ID
  DEBUG_SERIAL.print("Writing new ID... ");
  if (!dxl.writeControlTableItem(ControlTableItem::ID, CURRENT_ID, NEW_ID)) {
    DEBUG_SERIAL.println("FAILED.");
    return;
  }
  DEBUG_SERIAL.println("OK");

  delay(300);

  // Verify
  DEBUG_SERIAL.print("Verifying new ID... ");
  if (dxl.ping(NEW_ID)) {
    DEBUG_SERIAL.print("SUCCESS. Servo now responds at ID ");
    DEBUG_SERIAL.println(NEW_ID);
  } else {
    DEBUG_SERIAL.println("FAILED. Servo did not respond at new ID.");
  }
}

void loop() {
  // Nothing — ID change is a one-shot operation
}

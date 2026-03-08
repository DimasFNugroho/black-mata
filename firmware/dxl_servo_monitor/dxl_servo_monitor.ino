/**
 * dxl_servo_monitor.ino
 *
 * Continuously monitors a Dynamixel AX-12A servo and streams its
 * state as CSV lines over USB Serial.
 *
 * Configuration:
 *   Set SERVO_ID and BAUD_RATE below, then compile and flash.
 *
 * Output format (115200 baud USB Serial):
 *   STATUS,<ms>,<id>,<mode>,<position_deg>,<speed_rpm>,<load_pct>,<voltage_V>,<temp_C>
 *
 * Mode values:
 *   JOINT  — position control (CW or CCW angle limit is non-zero)
 *   WHEEL  — continuous rotation (both angle limits are 0)
 */

#include <Dynamixel2Arduino.h>

// ── User configuration ────────────────────────────────────────────────────────
#define SERVO_ID     1
#define BAUD_RATE    1000000
#define UPDATE_MS    200     // Stream interval in milliseconds
// ─────────────────────────────────────────────────────────────────────────────

#define DEBUG_SERIAL  Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28
#define DXL_PROTOCOL  1.0f

// AX-12A: 0–1023 ticks = 0–300 degrees
#define TICKS_TO_DEG(t)  ((t) * 300.0f / 1023.0f)
// AX-12A: speed in wheel mode = value * 0.111 RPM
#define TICKS_TO_RPM(t)  ((t) * 0.111f)
// AX-12A: load = value / 1023 * 100 % (bit 10 = direction)
#define TICKS_TO_LOAD(t) (((t) & 0x3FF) * 100.0f / 1023.0f)

// AX-12A raw addresses (Protocol 1.0)
#define ADDR_CW_LIMIT   6
#define ADDR_CCW_LIMIT  8

Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);

bool servoFound = false;

String getMode() {
  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  SERVO_ID);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, SERVO_ID);
  return (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";
}

void setup() {
  DEBUG_SERIAL.begin(115200);
  while (!DEBUG_SERIAL) delay(10);

  dxl.begin(BAUD_RATE);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  DEBUG_SERIAL.println("# ==============================================");
  DEBUG_SERIAL.print("# Dynamixel Servo Monitor — ID: ");
  DEBUG_SERIAL.println(SERVO_ID);
  DEBUG_SERIAL.println("# ==============================================");
  DEBUG_SERIAL.println("# FORMAT: STATUS,ms,id,mode,position_deg,speed_rpm,load_pct,voltage_V,temp_C");
  DEBUG_SERIAL.println("#");

  DEBUG_SERIAL.print("# Pinging ID ");
  DEBUG_SERIAL.print(SERVO_ID);
  DEBUG_SERIAL.print("... ");

  if (!dxl.ping(SERVO_ID)) {
    DEBUG_SERIAL.println("NOT FOUND. Check wiring and ID.");
    return;
  }

  DEBUG_SERIAL.println("OK");
  DEBUG_SERIAL.print("# Model   : "); DEBUG_SERIAL.println(dxl.getModelNumber(SERVO_ID));
  DEBUG_SERIAL.print("# FW ver  : "); DEBUG_SERIAL.println(dxl.readControlTableItem(ControlTableItem::FIRMWARE_VERSION, SERVO_ID));
  DEBUG_SERIAL.print("# Mode    : "); DEBUG_SERIAL.println(getMode());
  DEBUG_SERIAL.println("#");

  servoFound = true;
}

void loop() {
  if (!servoFound) {
    delay(2000);
    // Retry connection
    if (dxl.ping(SERVO_ID)) {
      DEBUG_SERIAL.println("# Servo reconnected.");
      servoFound = true;
    }
    return;
  }

  uint32_t ts  = millis();

  int32_t  pos   = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION,      SERVO_ID);
  int32_t  spd   = dxl.readControlTableItem(ControlTableItem::PRESENT_VELOCITY,      SERVO_ID);
  int32_t  load  = dxl.readControlTableItem(ControlTableItem::PRESENT_LOAD,          SERVO_ID);
  int32_t  volt  = dxl.readControlTableItem(ControlTableItem::PRESENT_INPUT_VOLTAGE, SERVO_ID);
  int32_t  temp  = dxl.readControlTableItem(ControlTableItem::PRESENT_TEMPERATURE,   SERVO_ID);
  String   mode  = getMode();

  if (pos < 0) {
    DEBUG_SERIAL.println("# ERROR: lost connection to servo.");
    servoFound = false;
    return;
  }

  DEBUG_SERIAL.print("STATUS,");
  DEBUG_SERIAL.print(ts);                             DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(SERVO_ID);                       DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(mode);                           DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(TICKS_TO_DEG(pos),   2);         DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(TICKS_TO_RPM(spd),   2);         DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(TICKS_TO_LOAD(load), 2);         DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.print(volt * 0.1f,          1);         DEBUG_SERIAL.print(",");
  DEBUG_SERIAL.println(temp);

  delay(UPDATE_MS);
}

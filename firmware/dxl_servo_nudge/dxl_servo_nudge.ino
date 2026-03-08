/**
 * dxl_servo_nudge.ino
 *
 * Moves an AX-12A servo +NUDGE_DEG degrees from its current position,
 * waits for motion to complete, then moves back to the original position.
 * Repeats every REPEAT_INTERVAL_MS milliseconds.
 *
 * If the servo is in WHEEL mode, it is temporarily switched to JOINT
 * mode for the nudge, then restored to WHEEL mode afterwards.
 *
 * Configuration:
 *   Set SERVO_ID, NUDGE_DEG, and REPEAT_INTERVAL_MS below.
 *
 * Output: 115200 baud USB Serial.
 */

#include <Dynamixel2Arduino.h>

// ── User configuration ────────────────────────────────────────────────────────
#define SERVO_ID            1
#define BAUD_RATE           1000000
#define NUDGE_DEG           5.0f    // Degrees to nudge (positive = CCW)
#define MOVE_SPEED          200     // Goal speed in ticks (0 = max, 1–1023)
#define REPEAT_INTERVAL_MS  3000    // Wait between nudge cycles
// ─────────────────────────────────────────────────────────────────────────────

#define DEBUG_SERIAL  Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28
#define DXL_PROTOCOL  1.0f

// AX-12A position conversion
#define DEG_TO_TICKS(d)  ((int32_t)((d) * 1023.0f / 300.0f))
#define TICKS_TO_DEG(t)  ((t) * 300.0f / 1023.0f)

#define AX12A_MAX_TICKS   1023
#define AX12A_MIN_TICKS   0
// Default joint mode limits (full range)
#define AX12A_DEFAULT_CW  0
#define AX12A_DEFAULT_CCW 1023

Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);

bool     servoReady   = false;
uint16_t saved_cw     = 0;
uint16_t saved_ccw    = 0;
bool     wasWheelMode = false;

bool isWheelMode(uint16_t cw, uint16_t ccw) {
  return (cw == 0 && ccw == 0);
}

void setJointMode() {
  dxl.torqueOff(SERVO_ID);
  dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  SERVO_ID, AX12A_DEFAULT_CW);
  dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, SERVO_ID, AX12A_DEFAULT_CCW);
  delay(100);
  dxl.torqueOn(SERVO_ID);
}

void restoreMode() {
  dxl.torqueOff(SERVO_ID);
  dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  SERVO_ID, saved_cw);
  dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, SERVO_ID, saved_ccw);
  delay(100);
}

void waitForMotion() {
  delay(300);
  uint32_t timeout = millis() + 3000;
  while (millis() < timeout) {
    int32_t moving = dxl.readControlTableItem(ControlTableItem::MOVING, SERVO_ID);
    if (moving == 0) break;
    delay(20);
  }
}

void moveTo(int32_t ticks) {
  ticks = constrain(ticks, AX12A_MIN_TICKS, AX12A_MAX_TICKS);
  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, SERVO_ID, MOVE_SPEED);
  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, SERVO_ID, ticks);
}

void setup() {
  DEBUG_SERIAL.begin(115200);
  while (!DEBUG_SERIAL) delay(10);

  dxl.begin(BAUD_RATE);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  DEBUG_SERIAL.println("==============================================");
  DEBUG_SERIAL.print(" Dynamixel Servo Nudge — ID: ");
  DEBUG_SERIAL.println(SERVO_ID);
  DEBUG_SERIAL.print(" Nudge: +/- ");
  DEBUG_SERIAL.print(NUDGE_DEG);
  DEBUG_SERIAL.println(" degrees");
  DEBUG_SERIAL.println("==============================================");

  if (!dxl.ping(SERVO_ID)) {
    DEBUG_SERIAL.println("ERROR: Servo not found. Check wiring and ID.");
    return;
  }
  DEBUG_SERIAL.println("Servo found.");

  // Save current mode
  saved_cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  SERVO_ID);
  saved_ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, SERVO_ID);
  wasWheelMode = isWheelMode(saved_cw, saved_ccw);

  if (wasWheelMode) {
    DEBUG_SERIAL.println("Mode: WHEEL — temporarily switching to JOINT for nudge.");
    setJointMode();
    DEBUG_SERIAL.println("Switched to JOINT mode.");
  } else {
    DEBUG_SERIAL.println("Mode: JOINT — OK");
    dxl.torqueOn(SERVO_ID);
  }

  servoReady = true;
  DEBUG_SERIAL.println("Ready. Starting nudge cycle...\n");
}

void loop() {
  if (!servoReady) {
    delay(2000);
    return;
  }

  // Read current position
  int32_t origin = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, SERVO_ID);
  if (origin < 0) {
    DEBUG_SERIAL.println("ERROR: Could not read position.");
    return;
  }

  float   origin_deg = TICKS_TO_DEG(origin);
  int32_t nudge_pos  = DEG_TO_TICKS(origin_deg + NUDGE_DEG);
  nudge_pos          = constrain(nudge_pos, AX12A_MIN_TICKS, AX12A_MAX_TICKS);

  DEBUG_SERIAL.print("Origin  : ");
  DEBUG_SERIAL.print(origin_deg, 2);
  DEBUG_SERIAL.print(" deg (tick ");
  DEBUG_SERIAL.print(origin);
  DEBUG_SERIAL.println(")");

  // Move to nudge position
  DEBUG_SERIAL.print("Nudging : +");
  DEBUG_SERIAL.print(NUDGE_DEG);
  DEBUG_SERIAL.println(" deg...");
  moveTo(nudge_pos);
  waitForMotion();

  int32_t after = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, SERVO_ID);
  DEBUG_SERIAL.print("Reached : ");
  DEBUG_SERIAL.print(TICKS_TO_DEG(after), 2);
  DEBUG_SERIAL.println(" deg");

  delay(500);

  // Return to origin
  DEBUG_SERIAL.println("Returning to origin...");
  moveTo(origin);
  waitForMotion();

  int32_t returned = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, SERVO_ID);
  DEBUG_SERIAL.print("Returned: ");
  DEBUG_SERIAL.print(TICKS_TO_DEG(returned), 2);
  DEBUG_SERIAL.println(" deg");

  // Restore original mode after each cycle
  if (wasWheelMode) {
    restoreMode();
    DEBUG_SERIAL.println("Restored WHEEL mode.");
  }

  DEBUG_SERIAL.println();
  delay(REPEAT_INTERVAL_MS);

  // Re-enter joint mode if needed for next cycle
  if (wasWheelMode) {
    setJointMode();
  }
}

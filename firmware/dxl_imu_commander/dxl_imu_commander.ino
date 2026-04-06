/**
 * dxl_imu_commander.ino
 *
 * Combined Dynamixel commander + BNO080 IMU streamer for OpenCM9.04.
 * Accepts text commands over USB Serial (same protocol as dxl_commander)
 * and simultaneously streams BNO080 IMU data as CSV lines.
 *
 * The IMU runs non-blocking via millis() — it keeps streaming even while
 * DXL commands are being processed. imuUpdate() is also called inside
 * MONITOR and waitForMotion() to minimise gaps.
 *
 * Wiring:
 *   OpenCM9.04 3-pin TTL Dynamixel connector → servo chain
 *   OpenCM9.04 USB                           → Jetson Nano (host)
 *   BNO080 SPI1:
 *     CS   → Pin 11
 *     SCK  → Pin 1
 *     MISO → Pin 6
 *     MOSI → Pin 7
 *     INT  → Pin 12
 *     RST  → Pin 13
 *     WAK  → Pin 14
 *
 * Libraries required:
 *   - Dynamixel2Arduino  (arduino-cli lib install "Dynamixel2Arduino")
 *   - SparkFun BNO080    (arduino-cli lib install "SparkFun BNO080 Cortex Based IMU")
 *
 * USB Serial: 115200 baud
 * Dynamixel bus: 1000000 baud (default, configurable via SETBAUD)
 *
 * ─── Output line prefixes ────────────────────────────────────────────────────
 *
 *  IMU lines (continuous, interleaved):
 *    QUAT,<ms>,<i>,<j>,<k>,<real>,<rad_accuracy>
 *    ACCEL,<ms>,<x>,<y>,<z>          (m/s^2)
 *    GYRO,<ms>,<x>,<y>,<z>           (rad/s)
 *    LINACC,<ms>,<x>,<y>,<z>         (m/s^2, gravity removed)
 *    GRAV,<ms>,<x>,<y>,<z>           (m/s^2)
 *    MAG,<ms>,<x>,<y>,<z>            (uTesla)
 *
 *  DXL response lines (on demand):
 *    OK,<CMD>,...
 *    ERR,<CMD>,...
 *    FOUND,<id>,...
 *    STATUS,<ms>,<id>,...   (from MONITOR)
 *    VOLTAGE,<id>,...
 *    NUDGE,...
 *
 *  Comments / info:  # ...
 *
 * ─── Command Reference ───────────────────────────────────────────────────────
 *
 * IMU commands:
 *   IMUON                 Enable IMU streaming (default: on)
 *   IMUOFF                Disable IMU streaming
 *   IMURATE <ms>          Set IMU report interval in ms (default: 50 = 20 Hz)
 *
 * Dynamixel commands (unchanged from dxl_commander):
 *   PING <id>
 *   SCAN [<max_id>]
 *   MONITOR <id> [<interval_ms>]
 *   NUDGE <id> [<degrees>] [<speed>]
 *   GETPOS <id>
 *   SETPOS <id> <ticks> [<speed>]
 *   GETSPEED <id>
 *   SETSPEED <id> <speed>
 *   GETMODE <id>
 *   SETMODE <id> <JOINT|WHEEL>
 *   VOLTAGE <id>
 *   VOLTAGES [<max_id>]
 *   IDCHANGE <current_id> <new_id>
 *   SETBAUD <baud>
 *   TORQUE <id> <0|1>
 *   HELP
 *
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <Dynamixel2Arduino.h>
#include <SPI.h>
#include "SparkFun_BNO080_Arduino_Library.h"

// ── Hardware ──────────────────────────────────────────────────────────────────
#define USB_SERIAL    Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28

#define USB_BAUD      115200
#define DXL_BAUD      1000000UL

// BNO080 SPI1 pins (OpenCM9.04)
#define IMU_CS   11
#define IMU_WAK  14
#define IMU_INT  12
#define IMU_RST  13
#define SPI_CLOCK 3000000

// ── AX-12A constants ─────────────────────────────────────────────────────────
#define AX12A_MAX_TICKS   1023
#define TICKS_TO_DEG(t)   ((t) * 300.0f / 1023.0f)
#define DEG_TO_TICKS(d)   ((int32_t)((d) * 1023.0f / 300.0f))
#define TICKS_TO_RPM(t)   (((t) & 0x3FF) * 0.111f)
#define TICKS_TO_LOAD(t)  (((t) & 0x3FF) * 100.0f / 1023.0f)

#define DXL_PROTOCOL  1.0f
#define INPUT_BUF_SIZE 128

// ── Globals ──────────────────────────────────────────────────────────────────
Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);
BNO080 imu;

uint32_t dxlBaud = DXL_BAUD;
char inputBuf[INPUT_BUF_SIZE];
uint8_t inputPos = 0;

bool     imuEnabled     = true;
uint32_t imuIntervalMs  = 50;   // 20 Hz default
uint32_t lastImuMs      = 0;
bool     imuReady       = false;

// ── IMU ───────────────────────────────────────────────────────────────────────

void imuInit() {
  SPI.begin();
  if (!imu.beginSPI(IMU_CS, IMU_WAK, IMU_INT, IMU_RST, SPI_CLOCK, SPI)) {
    USB_SERIAL.println("# WARNING: BNO080 not detected — IMU disabled");
    imuReady = false;
    imuEnabled = false;
    return;
  }
  imu.enableRotationVector(imuIntervalMs);
  imu.enableAccelerometer(imuIntervalMs);
  imu.enableGyro(imuIntervalMs);
  imu.enableLinearAccelerometer(imuIntervalMs);
  imu.enableGravity(imuIntervalMs);
  imu.enableMagnetometer(imuIntervalMs);
  imuReady = true;
}

// Call this frequently — outputs one batch of IMU lines when data is available
// and the interval has elapsed.
void imuUpdate() {
  if (!imuEnabled || !imuReady) return;

  uint32_t now = millis();
  if (now - lastImuMs < imuIntervalMs) return;

  if (!imu.dataAvailable()) return;
  lastImuMs = now;

  uint32_t ts = now;

  USB_SERIAL.print("QUAT,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getQuatI(), 6);       USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getQuatJ(), 6);       USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getQuatK(), 6);       USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getQuatReal(), 6);    USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getQuatRadianAccuracy(), 6);

  USB_SERIAL.print("ACCEL,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getAccelX(), 4);      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getAccelY(), 4);      USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getAccelZ(), 4);

  USB_SERIAL.print("GYRO,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getGyroX(), 4);       USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getGyroY(), 4);       USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getGyroZ(), 4);

  USB_SERIAL.print("LINACC,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getLinAccelX(), 4);   USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getLinAccelY(), 4);   USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getLinAccelZ(), 4);

  USB_SERIAL.print("GRAV,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getGravityX(), 4);    USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getGravityY(), 4);    USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getGravityZ(), 4);

  USB_SERIAL.print("MAG,");
  USB_SERIAL.print(ts);                      USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getMagX(), 4);        USB_SERIAL.print(",");
  USB_SERIAL.print(imu.getMagY(), 4);        USB_SERIAL.print(",");
  USB_SERIAL.println(imu.getMagZ(), 4);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

String getMode(uint8_t id) {
  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  return (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";
}

void waitForMotion(uint8_t id, uint32_t timeoutMs = 3000) {
  delay(300);
  uint32_t deadline = millis() + timeoutMs;
  while (millis() < deadline) {
    imuUpdate();
    int32_t moving = dxl.readControlTableItem(ControlTableItem::MOVING, id);
    if (moving == 0) break;
    delay(20);
  }
}

String nextToken(const char* cmd, uint8_t &pos) {
  while (cmd[pos] == ' ' || cmd[pos] == '\t') pos++;
  if (cmd[pos] == '\0') return "";

  uint8_t start = pos;
  while (cmd[pos] != '\0' && cmd[pos] != ' ' && cmd[pos] != '\t') pos++;

  char token[32];
  uint8_t len = pos - start;
  if (len >= sizeof(token)) len = sizeof(token) - 1;
  memcpy(token, cmd + start, len);
  token[len] = '\0';
  return String(token);
}

int32_t tokenInt(const char* cmd, uint8_t &pos, int32_t fallback) {
  String t = nextToken(cmd, pos);
  if (t.length() == 0) return fallback;
  return t.toInt();
}

float tokenFloat(const char* cmd, uint8_t &pos, float fallback) {
  String t = nextToken(cmd, pos);
  if (t.length() == 0) return fallback;
  return t.toFloat();
}

// ── IMU command handlers ──────────────────────────────────────────────────────

void cmdImuOn() {
  if (!imuReady) { USB_SERIAL.println("ERR,IMUON,NOT_INITIALIZED"); return; }
  imuEnabled = true;
  USB_SERIAL.println("OK,IMUON");
}

void cmdImuOff() {
  imuEnabled = false;
  USB_SERIAL.println("OK,IMUOFF");
}

void cmdImuRate(const char* args) {
  uint8_t pos = 0;
  int32_t ms = tokenInt(args, pos, -1);
  if (ms < 10 || ms > 10000) { USB_SERIAL.println("ERR,IMURATE,INVALID (10-10000ms)"); return; }

  imuIntervalMs = (uint32_t)ms;
  if (imuReady) {
    imu.enableRotationVector(imuIntervalMs);
    imu.enableAccelerometer(imuIntervalMs);
    imu.enableGyro(imuIntervalMs);
    imu.enableLinearAccelerometer(imuIntervalMs);
    imu.enableGravity(imuIntervalMs);
    imu.enableMagnetometer(imuIntervalMs);
  }
  USB_SERIAL.print("OK,IMURATE,");
  USB_SERIAL.println(imuIntervalMs);
}

// ── DXL command handlers ──────────────────────────────────────────────────────

void cmdPing(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,PING,INVALID_ID"); return; }
  if (dxl.ping(id)) {
    USB_SERIAL.print("OK,PING,");
    USB_SERIAL.print(id);
    USB_SERIAL.print(",");
    USB_SERIAL.println(dxl.getModelNumber(id));
  } else {
    USB_SERIAL.print("ERR,PING,");
    USB_SERIAL.print(id);
    USB_SERIAL.println(",NOT_FOUND");
  }
}

void cmdScan(const char* args) {
  uint8_t pos = 0;
  int32_t maxId = tokenInt(args, pos, 252);
  if (maxId < 1) maxId = 1;
  if (maxId > 252) maxId = 252;

  uint8_t count = 0;
  for (int id = 1; id <= maxId; id++) {
    imuUpdate();
    if (dxl.ping(id)) {
      count++;
      USB_SERIAL.print("FOUND,");
      USB_SERIAL.print(id);
      USB_SERIAL.print(",");
      USB_SERIAL.print(dxl.getModelNumber(id));
      USB_SERIAL.print(",");
      USB_SERIAL.print(dxl.readControlTableItem(ControlTableItem::FIRMWARE_VERSION, id));
      USB_SERIAL.print(",");
      USB_SERIAL.println(getMode(id));
    }
  }
  USB_SERIAL.print("OK,SCAN,");
  USB_SERIAL.println(count);
}

void cmdMonitor(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  int32_t intervalMs = tokenInt(args, pos, 200);

  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,MONITOR,INVALID_ID"); return; }
  if (!dxl.ping(id)) {
    USB_SERIAL.print("ERR,MONITOR,");
    USB_SERIAL.print(id);
    USB_SERIAL.println(",NOT_FOUND");
    return;
  }

  USB_SERIAL.print("# MONITOR,START,");
  USB_SERIAL.print(id);
  USB_SERIAL.print(",");
  USB_SERIAL.print(intervalMs);
  USB_SERIAL.println("ms");
  USB_SERIAL.println("# FORMAT: STATUS,ms,id,mode,pos_deg,speed_rpm,load_pct,voltage_V,temp_C");

  while (USB_SERIAL.available()) USB_SERIAL.read();

  uint32_t t0 = millis();
  uint32_t lastDxlMs = 0;

  while (true) {
    if (USB_SERIAL.available()) {
      while (USB_SERIAL.available()) USB_SERIAL.read();
      break;
    }

    imuUpdate();

    uint32_t now = millis();
    if (now - lastDxlMs >= (uint32_t)intervalMs) {
      lastDxlMs = now;
      uint32_t ts = now - t0;

      int32_t posVal = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
      if (posVal < 0) {
        USB_SERIAL.println("ERR,MONITOR,LOST_CONNECTION");
        break;
      }
      int32_t spd  = dxl.readControlTableItem(ControlTableItem::PRESENT_VELOCITY, id);
      int32_t load = dxl.readControlTableItem(ControlTableItem::PRESENT_LOAD, id);
      int32_t volt = dxl.readControlTableItem(ControlTableItem::PRESENT_INPUT_VOLTAGE, id);
      int32_t temp = dxl.readControlTableItem(ControlTableItem::PRESENT_TEMPERATURE, id);
      String  mode = getMode(id);

      USB_SERIAL.print("STATUS,");
      USB_SERIAL.print(ts);                         USB_SERIAL.print(",");
      USB_SERIAL.print(id);                         USB_SERIAL.print(",");
      USB_SERIAL.print(mode);                       USB_SERIAL.print(",");
      USB_SERIAL.print(TICKS_TO_DEG(posVal), 2);    USB_SERIAL.print(",");
      USB_SERIAL.print(TICKS_TO_RPM(spd), 2);       USB_SERIAL.print(",");
      USB_SERIAL.print(TICKS_TO_LOAD(load), 2);     USB_SERIAL.print(",");
      USB_SERIAL.print(volt * 0.1f, 1);             USB_SERIAL.print(",");
      USB_SERIAL.println(temp);
    }
  }
  USB_SERIAL.println("OK,MONITOR,STOPPED");
}

void cmdNudge(const char* args) {
  uint8_t pos = 0;
  int32_t id       = tokenInt(args, pos, -1);
  float   nudgeDeg = tokenFloat(args, pos, 5.0f);
  int32_t speed    = tokenInt(args, pos, 200);

  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,NUDGE,INVALID_ID"); return; }
  if (!dxl.ping(id)) {
    USB_SERIAL.print("ERR,NUDGE,");
    USB_SERIAL.print(id);
    USB_SERIAL.println(",NOT_FOUND");
    return;
  }

  uint16_t savedCw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT, id);
  uint16_t savedCcw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  bool wasWheel = (savedCw == 0 && savedCcw == 0);

  if (wasWheel) {
    dxl.torqueOff(id);
    dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, 0);
    dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, 1023);
    delay(100);
    dxl.torqueOn(id);
  } else {
    dxl.torqueOn(id);
  }

  int32_t origin = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
  float originDeg = TICKS_TO_DEG(origin);
  int32_t nudgePos = DEG_TO_TICKS(originDeg + nudgeDeg);
  nudgePos = constrain(nudgePos, 0, AX12A_MAX_TICKS);

  USB_SERIAL.print("NUDGE,ORIGIN,");
  USB_SERIAL.println(originDeg, 2);

  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, speed);
  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, nudgePos);
  waitForMotion(id);

  int32_t afterPos = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
  USB_SERIAL.print("NUDGE,REACHED,");
  USB_SERIAL.println(TICKS_TO_DEG(afterPos), 2);

  delay(500);

  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, origin);
  waitForMotion(id);

  int32_t retPos = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
  USB_SERIAL.print("NUDGE,RETURNED,");
  USB_SERIAL.println(TICKS_TO_DEG(retPos), 2);

  if (wasWheel) {
    dxl.torqueOff(id);
    dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, savedCw);
    dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, savedCcw);
    delay(100);
  }

  USB_SERIAL.print("OK,NUDGE,");
  USB_SERIAL.println(id);
}

void cmdGetPos(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETPOS,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETPOS,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  int32_t ticks = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
  USB_SERIAL.print("OK,GETPOS,");
  USB_SERIAL.print(id);    USB_SERIAL.print(",");
  USB_SERIAL.print(ticks); USB_SERIAL.print(",");
  USB_SERIAL.println(TICKS_TO_DEG(ticks), 2);
}

void cmdSetPos(const char* args) {
  uint8_t pos = 0;
  int32_t id    = tokenInt(args, pos, -1);
  int32_t ticks = tokenInt(args, pos, -1);
  int32_t speed = tokenInt(args, pos, 200);

  if (id < 1 || id > 252)              { USB_SERIAL.println("ERR,SETPOS,INVALID_ID"); return; }
  if (ticks < 0 || ticks > AX12A_MAX_TICKS) { USB_SERIAL.println("ERR,SETPOS,INVALID_TICKS"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,SETPOS,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  dxl.torqueOn(id);
  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, speed);
  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, ticks);

  USB_SERIAL.print("OK,SETPOS,");
  USB_SERIAL.print(id);    USB_SERIAL.print(",");
  USB_SERIAL.println(ticks);
}

void cmdGetSpeed(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETSPEED,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETSPEED,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  int32_t spd = dxl.readControlTableItem(ControlTableItem::PRESENT_VELOCITY, id);
  USB_SERIAL.print("OK,GETSPEED,");
  USB_SERIAL.print(id);                   USB_SERIAL.print(",");
  USB_SERIAL.print(spd);                  USB_SERIAL.print(",");
  USB_SERIAL.println(TICKS_TO_RPM(spd), 2);
}

void cmdSetSpeed(const char* args) {
  uint8_t pos = 0;
  int32_t id    = tokenInt(args, pos, -1);
  int32_t speed = tokenInt(args, pos, -1);

  if (id < 1 || id > 252)      { USB_SERIAL.println("ERR,SETSPEED,INVALID_ID"); return; }
  if (speed < 0 || speed > 2047) { USB_SERIAL.println("ERR,SETSPEED,INVALID_SPEED"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,SETSPEED,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, speed);

  USB_SERIAL.print("OK,SETSPEED,");
  USB_SERIAL.print(id);    USB_SERIAL.print(",");
  USB_SERIAL.println(speed);
}

void cmdGetMode(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETMODE,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETMODE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  String mode = (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";

  USB_SERIAL.print("OK,GETMODE,");
  USB_SERIAL.print(id);   USB_SERIAL.print(",");
  USB_SERIAL.print(mode); USB_SERIAL.print(",");
  USB_SERIAL.print(cw);   USB_SERIAL.print(",");
  USB_SERIAL.println(ccw);
}

void cmdSetMode(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  String mode = nextToken(args, pos);
  mode.toUpperCase();

  if (id < 1 || id > 252)                    { USB_SERIAL.println("ERR,SETMODE,INVALID_ID"); return; }
  if (mode != "JOINT" && mode != "WHEEL")    { USB_SERIAL.println("ERR,SETMODE,INVALID_MODE"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,SETMODE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  dxl.torqueOff(id);

  if (mode == "WHEEL") {
    dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, 0);
    dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, 0);
  } else {
    dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, 0);
    dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, 1023);
  }
  delay(100);

  USB_SERIAL.print("OK,SETMODE,");
  USB_SERIAL.print(id);   USB_SERIAL.print(",");
  USB_SERIAL.println(mode);
}

void cmdVoltage(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,VOLTAGE,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,VOLTAGE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  int32_t volt = dxl.readControlTableItem(ControlTableItem::PRESENT_INPUT_VOLTAGE, id);
  USB_SERIAL.print("OK,VOLTAGE,");
  USB_SERIAL.print(id);               USB_SERIAL.print(",");
  USB_SERIAL.println(volt * 0.1f, 1);
}

void cmdVoltages(const char* args) {
  uint8_t pos = 0;
  int32_t maxId = tokenInt(args, pos, 252);
  if (maxId < 1) maxId = 1;
  if (maxId > 252) maxId = 252;

  uint8_t count = 0;
  for (int id = 1; id <= maxId; id++) {
    imuUpdate();
    if (dxl.ping(id)) {
      count++;
      int32_t volt = dxl.readControlTableItem(ControlTableItem::PRESENT_INPUT_VOLTAGE, id);
      USB_SERIAL.print("VOLTAGE,");
      USB_SERIAL.print(id);              USB_SERIAL.print(",");
      USB_SERIAL.println(volt * 0.1f, 1);
    }
  }
  USB_SERIAL.print("OK,VOLTAGES,");
  USB_SERIAL.println(count);
}

void cmdIdChange(const char* args) {
  uint8_t pos = 0;
  int32_t currentId = tokenInt(args, pos, -1);
  int32_t newId     = tokenInt(args, pos, -1);

  if (currentId < 1 || currentId > 252) { USB_SERIAL.println("ERR,IDCHANGE,INVALID_CURRENT_ID"); return; }
  if (newId < 1 || newId > 252)         { USB_SERIAL.println("ERR,IDCHANGE,INVALID_NEW_ID"); return; }
  if (currentId == newId)               { USB_SERIAL.println("ERR,IDCHANGE,SAME_ID"); return; }

  if (!dxl.ping(currentId)) {
    USB_SERIAL.print("ERR,IDCHANGE,");
    USB_SERIAL.print(currentId);
    USB_SERIAL.println(",NOT_FOUND");
    return;
  }
  if (dxl.ping(newId)) {
    USB_SERIAL.print("ERR,IDCHANGE,");
    USB_SERIAL.print(newId);
    USB_SERIAL.println(",CONFLICT");
    return;
  }

  dxl.torqueOff(currentId);
  if (!dxl.writeControlTableItem(ControlTableItem::ID, currentId, newId)) {
    USB_SERIAL.println("ERR,IDCHANGE,WRITE_FAILED");
    return;
  }
  delay(300);

  if (dxl.ping(newId)) {
    USB_SERIAL.print("OK,IDCHANGE,");
    USB_SERIAL.print(currentId);
    USB_SERIAL.print(",");
    USB_SERIAL.println(newId);
  } else {
    USB_SERIAL.println("ERR,IDCHANGE,VERIFY_FAILED");
  }
}

void cmdSetBaud(const char* args) {
  uint8_t pos = 0;
  int32_t baud = tokenInt(args, pos, -1);
  if (baud <= 0) { USB_SERIAL.println("ERR,SETBAUD,INVALID"); return; }

  dxlBaud = baud;
  dxl.begin(dxlBaud);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  USB_SERIAL.print("OK,SETBAUD,");
  USB_SERIAL.println(dxlBaud);
}

void cmdTorque(const char* args) {
  uint8_t pos = 0;
  int32_t id  = tokenInt(args, pos, -1);
  int32_t val = tokenInt(args, pos, -1);

  if (id < 1 || id > 252)   { USB_SERIAL.println("ERR,TORQUE,INVALID_ID"); return; }
  if (val != 0 && val != 1) { USB_SERIAL.println("ERR,TORQUE,INVALID_VALUE"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,TORQUE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }

  if (val == 1) dxl.torqueOn(id);
  else          dxl.torqueOff(id);

  USB_SERIAL.print("OK,TORQUE,");
  USB_SERIAL.print(id);  USB_SERIAL.print(",");
  USB_SERIAL.println(val);
}

void cmdHelp() {
  USB_SERIAL.println("=== dxl_imu_commander commands ===");
  USB_SERIAL.println("--- IMU ---");
  USB_SERIAL.println("IMUON                         Enable IMU streaming");
  USB_SERIAL.println("IMUOFF                        Disable IMU streaming");
  USB_SERIAL.println("IMURATE <ms>                  Set IMU interval (10-10000ms, default 50)");
  USB_SERIAL.println("--- Dynamixel ---");
  USB_SERIAL.println("PING <id>                     Ping servo");
  USB_SERIAL.println("SCAN [max_id]                 Scan for servos");
  USB_SERIAL.println("MONITOR <id> [interval_ms]    Stream servo state (any char to stop)");
  USB_SERIAL.println("NUDGE <id> [deg] [speed]      Nudge +deg and return");
  USB_SERIAL.println("GETPOS <id>                   Read position");
  USB_SERIAL.println("SETPOS <id> <ticks> [speed]   Move to position (0-1023)");
  USB_SERIAL.println("GETSPEED <id>                 Read speed");
  USB_SERIAL.println("SETSPEED <id> <speed>         Set moving speed");
  USB_SERIAL.println("GETMODE <id>                  Read mode (JOINT/WHEEL)");
  USB_SERIAL.println("SETMODE <id> <JOINT|WHEEL>    Set mode");
  USB_SERIAL.println("VOLTAGE <id>                  Read voltage");
  USB_SERIAL.println("VOLTAGES [max_id]             Read all servo voltages");
  USB_SERIAL.println("IDCHANGE <old_id> <new_id>    Change servo ID");
  USB_SERIAL.println("SETBAUD <baud>                Change Dynamixel bus baud rate");
  USB_SERIAL.println("TORQUE <id> <0|1>             Enable/disable torque");
  USB_SERIAL.println("HELP                          Show this help");
}

// ── Command dispatch ──────────────────────────────────────────────────────────

void processCommand(const char* line) {
  while (*line == ' ' || *line == '\t') line++;
  if (*line == '\0' || *line == '#') return;

  uint8_t pos = 0;
  String cmd = nextToken(line, pos);
  cmd.toUpperCase();
  const char* args = line + pos;

  if      (cmd == "IMUON")    cmdImuOn();
  else if (cmd == "IMUOFF")   cmdImuOff();
  else if (cmd == "IMURATE")  cmdImuRate(args);
  else if (cmd == "PING")     cmdPing(args);
  else if (cmd == "SCAN")     cmdScan(args);
  else if (cmd == "MONITOR")  cmdMonitor(args);
  else if (cmd == "NUDGE")    cmdNudge(args);
  else if (cmd == "GETPOS")   cmdGetPos(args);
  else if (cmd == "SETPOS")   cmdSetPos(args);
  else if (cmd == "GETSPEED") cmdGetSpeed(args);
  else if (cmd == "SETSPEED") cmdSetSpeed(args);
  else if (cmd == "GETMODE")  cmdGetMode(args);
  else if (cmd == "SETMODE")  cmdSetMode(args);
  else if (cmd == "VOLTAGE")  cmdVoltage(args);
  else if (cmd == "VOLTAGES") cmdVoltages(args);
  else if (cmd == "IDCHANGE") cmdIdChange(args);
  else if (cmd == "SETBAUD")  cmdSetBaud(args);
  else if (cmd == "TORQUE")   cmdTorque(args);
  else if (cmd == "HELP")     cmdHelp();
  else {
    USB_SERIAL.print("ERR,UNKNOWN_CMD,");
    USB_SERIAL.println(cmd);
  }
}

// ── Setup & Loop ──────────────────────────────────────────────────────────────

void setup() {
  USB_SERIAL.begin(USB_BAUD);
  while (!USB_SERIAL) delay(10);

  dxl.begin(dxlBaud);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  imuInit();

  USB_SERIAL.println("# dxl_imu_commander ready");
  USB_SERIAL.print("# DXL baud: ");
  USB_SERIAL.println(dxlBaud);
  USB_SERIAL.print("# IMU: ");
  USB_SERIAL.println(imuReady ? "OK" : "NOT FOUND");
  USB_SERIAL.println("# Send HELP for command list");
}

void loop() {
  // Non-blocking IMU stream
  imuUpdate();

  // Command input
  while (USB_SERIAL.available()) {
    char c = USB_SERIAL.read();
    if (c == '\n' || c == '\r') {
      if (inputPos > 0) {
        inputBuf[inputPos] = '\0';
        processCommand(inputBuf);
        inputPos = 0;
      }
    } else if (inputPos < INPUT_BUF_SIZE - 1) {
      inputBuf[inputPos++] = c;
    }
  }
}

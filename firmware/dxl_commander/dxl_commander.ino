/**
 * dxl_commander.ino
 *
 * Unified Dynamixel command firmware for OpenCM9.04.
 * Operates in two modes on the same USB Serial port:
 *
 *   TEXT MODE   — newline-terminated ASCII commands for diagnostic tools
 *                 (PING, SCAN, MONITOR, NUDGE, GETPOS, …).
 *                 Activated when the first byte of a message is NOT 0xAA.
 *
 *   BINARY MODE — fixed-length binary frames for the Robot Agent.
 *                 Activated when the first byte is 0xAA (START_BYTE).
 *                 Receives CMD frames, applies servo targets atomically,
 *                 reads servo state, and replies with STATE frames.
 *                 A firmware watchdog fires if no valid CMD frame arrives
 *                 within WATCHDOG_MS; it zeroes all drive (WHEEL) speeds.
 *
 * Wiring:
 *   OpenCM9.04 3-pin TTL Dynamixel connector → servo chain
 *   OpenCM9.04 USB → host (Jetson or x86)  — data only, no power
 *   OpenCM9.04 12V power input → 12V rail via onboard switch (master switch)
 *
 * USB Serial : 115200 baud
 * Dynamixel  : 1000000 baud, Protocol 1.0
 *
 * ─── Binary Frame Layout ─────────────────────────────────────────────
 *
 * CMD frame  (host → OpenCM): 105 bytes
 *   [0]       START       = 0xAA
 *   [1]       TYPE        = 0x01
 *   [2]       SEQ         uint8  (rolling 0–255)
 *   [3..6]    TIMESTAMP   uint32 (ms, little-endian)
 *   [7..38]   servo_cmd[8]  — 4 bytes each:
 *               [+0] mode          0=JOINT  1=WHEEL
 *               [+1] enable_torque 0=off    1=on
 *               [+2] target_lo     uint16 LE (ticks for JOINT, speed for WHEEL)
 *               [+3] target_hi
 *   [39..102] payload[64] (reserved, zeros)
 *   [103..104] CRC-16 CCITT (poly=0x1021, init=0xFFFF) over bytes [0..102]
 *
 * STATE frame (OpenCM → host): 202 bytes
 *   [0]       START       = 0xAA
 *   [1]       TYPE        = 0x02
 *   [2]       SEQ         echoes CMD seq
 *   [3..6]    TIMESTAMP   uint32 (ms, millis(), little-endian)
 *   [7]       e_stop      0=normal  1=watchdog fired
 *   [8..71]   imu[64]     (reserved, zeros — IMU not yet used)
 *   [72..135] servo_state[8] — 8 bytes each:
 *               [+0] available  0 or 1
 *               [+1] mode       0=JOINT  1=WHEEL
 *               [+2] pos_lo     uint16 LE (0–1023 ticks)
 *               [+3] pos_hi
 *               [+4] speed_lo   uint16 LE
 *               [+5] speed_hi
 *               [+6] temperature uint8 (°C)
 *               [+7] voltage    uint8 (raw; V = value × 0.1)
 *   [136..199] payload[64] (reserved, zeros)
 *   [200..201] CRC-16 CCITT over bytes [0..199]
 *
 * Practical control rate: ~25 Hz (limited by AX-12A Protocol 1.0
 * individual register reads — 16 DXL reads × ~1 ms each per frame).
 * Temperature and voltage are refreshed every SLOW_POLL_EVERY frames.
 *
 * ─── Text Command Reference ──────────────────────────────────────────
 *
 * PING <id>
 * SCAN [<max_id>]
 * MONITOR <id> [<interval_ms>]
 * NUDGE <id> [<degrees>] [<speed>]
 * GETPOS <id>
 * SETPOS <id> <ticks> [<speed>]
 * GETSPEED <id>
 * SETSPEED <id> <speed>
 * GETMODE <id>
 * SETMODE <id> <JOINT|WHEEL>
 * VOLTAGE <id>
 * VOLTAGES [<max_id>]
 * IDCHANGE <current_id> <new_id>
 * SETBAUD <baud>
 * TORQUE <id> <0|1>
 * HELP
 *
 * ─────────────────────────────────────────────────────────────────────
 */

#include <Dynamixel2Arduino.h>

// ── Hardware ──────────────────────────────────────────────────────────────────
#define USB_SERIAL    Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28

#define USB_BAUD      115200
#define DXL_BAUD      1000000UL

// ── AX-12A constants ─────────────────────────────────────────────────────────
#define AX12A_MAX_TICKS   1023
#define TICKS_TO_DEG(t)   ((t) * 300.0f / 1023.0f)
#define DEG_TO_TICKS(d)   ((int32_t)((d) * 1023.0f / 300.0f))
#define TICKS_TO_RPM(t)   (((t) & 0x3FF) * 0.111f)
#define TICKS_TO_LOAD(t)  (((t) & 0x3FF) * 100.0f / 1023.0f)

#define DXL_PROTOCOL  1.0f
#define INPUT_BUF_SIZE 128

// ── Binary protocol constants ─────────────────────────────────────────────────
#define FRAME_START           0xAA
#define FRAME_TYPE_CMD        0x01
#define FRAME_TYPE_STATE      0x02

#define NUM_SERVOS            8
#define CMD_FRAME_SIZE        105
#define STATE_FRAME_SIZE      202

// Byte offsets within CMD frame
#define CMD_OFF_SEQ           2
#define CMD_OFF_TS            3
#define CMD_OFF_SERVOS        7
#define CMD_SERVO_STRIDE      4

// Byte offsets within STATE frame
#define STATE_OFF_SEQ         2
#define STATE_OFF_TS          3
#define STATE_OFF_ESTOP       7
#define STATE_OFF_IMU         8   // 64 bytes reserved
#define STATE_OFF_SERVOS      72
#define STATE_SERVO_STRIDE    8

#define BINARY_READ_TIMEOUT_MS  50
#define WATCHDOG_MS             500

// ── Globals — text mode ───────────────────────────────────────────────────────
Dynamixel2Arduino dxl(DXL_SERIAL, DXL_DIR_PIN);
uint32_t dxlBaud = DXL_BAUD;
char inputBuf[INPUT_BUF_SIZE];
uint8_t inputPos = 0;

// ── Globals — binary mode ─────────────────────────────────────────────────────
static const uint8_t SERVO_IDS[NUM_SERVOS] = {1, 2, 3, 4, 5, 6, 7, 8};

static uint8_t  cmdFrameBuf[CMD_FRAME_SIZE];
static uint8_t  stateBuf[STATE_FRAME_SIZE];

// Per-servo cached state (updated each STATE frame build)
static bool     servoAvail[NUM_SERVOS];
static uint8_t  servoMode[NUM_SERVOS];      // 0=JOINT 1=WHEEL
static uint16_t servoPos[NUM_SERVOS];
static uint16_t servoSpeed[NUM_SERVOS];
static uint8_t  servoTemp[NUM_SERVOS];
static uint8_t  servoVolt[NUM_SERVOS];

static bool     binaryModeActive  = false;
static bool     eStopActive       = false;
static uint32_t lastCmdFrameMs    = 0;
static uint8_t  slowPollIdx       = 0;  // round-robin index for temp+volt reads

// ── Heartbeat LED ─────────────────────────────────────────────────────────────
#define HEARTBEAT_INTERVAL_MS 1000

static uint32_t lastLedToggleMs = 0;
static bool     ledState        = false;

static void updateHeartbeatLed() {
  if (binaryModeActive && !eStopActive) {
    if ((millis() - lastLedToggleMs) >= HEARTBEAT_INTERVAL_MS) {
      ledState = !ledState;
      digitalWrite(BOARD_LED_PIN, ledState ? LOW : HIGH);  // active low
      lastLedToggleMs = millis();
    }
  } else {
    digitalWrite(BOARD_LED_PIN, HIGH);  // off
    ledState        = false;
    lastLedToggleMs = millis();
  }
}

// ── CRC-16 CCITT (poly=0x1021, init=0xFFFF) ───────────────────────────────────
static uint16_t crc16(const uint8_t* data, uint16_t len) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t j = 0; j < 8; j++) {
      crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
    }
  }
  return crc;
}

// ── Binary helpers ────────────────────────────────────────────────────────────

static void putU32LE(uint8_t* dst, uint32_t v) {
  dst[0] = v & 0xFF;
  dst[1] = (v >> 8) & 0xFF;
  dst[2] = (v >> 16) & 0xFF;
  dst[3] = (v >> 24) & 0xFF;
}

static uint16_t getU16LE(const uint8_t* src) {
  return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

// ── Servo initialisation ──────────────────────────────────────────────────────

static void scanServos() {
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    servoAvail[i] = dxl.ping(SERVO_IDS[i]);
    if (servoAvail[i]) {
      uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  SERVO_IDS[i]);
      uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, SERVO_IDS[i]);
      servoMode[i] = (cw == 0 && ccw == 0) ? 1 : 0;
    }
  }
}

// ── E-stop ────────────────────────────────────────────────────────────────────
// 1. Zero speed on WHEEL servos first (clean stop before power cut).
// 2. Disable torque on all servos (no holding force — robot goes limp).

static void eStop() {
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    if (!servoAvail[i]) continue;
    if (servoMode[i] == 1) {
      dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, SERVO_IDS[i], 0);
    }
  }
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    if (!servoAvail[i]) continue;
    dxl.torqueOff(SERVO_IDS[i]);
  }
  eStopActive = true;
}

// ── Binary frame I/O ──────────────────────────────────────────────────────────

// Read the remaining (frameSize - 1) bytes after START_BYTE into buf[1..].
// Returns true if fully read and CRC is valid.
static bool readBinaryFrame(uint8_t* buf, uint16_t frameSize) {
  uint32_t deadline = millis() + BINARY_READ_TIMEOUT_MS;
  uint16_t got = 1;  // buf[0] already holds START_BYTE
  while (got < frameSize) {
    if ((int32_t)(millis() - deadline) > 0) return false;
    if (USB_SERIAL.available()) buf[got++] = (uint8_t)USB_SERIAL.read();
  }
  uint16_t expected = crc16(buf, frameSize - 2);
  uint16_t received = ((uint16_t)buf[frameSize - 2] << 8) | buf[frameSize - 1];
  return expected == received;
}

// Read servo state from Dynamixel bus and fill per-servo cache.
// Position and speed are read for all servos every frame.
// Temperature and voltage are read for one servo per frame in round-robin
// fashion to avoid congesting the bus with 16 extra reads at once.
static void refreshServoState() {
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    if (!servoAvail[i]) continue;
    uint8_t id = SERVO_IDS[i];

    int32_t pos = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
    // AX-12A Present Speed is at address 38 (2 bytes, 0-2047).
    // PRESENT_VELOCITY is a Protocol 2.0 item and returns -1 for AX series.
    uint8_t spdBuf[2] = {0, 0};
    bool spdOk = dxl.read(id, 38, 2, spdBuf, sizeof(spdBuf));

    // On a single read failure, keep last known values — do NOT mark unavailable.
    // A transient bus hiccup should not cause the next CMD frame to skip this servo.
    if (pos >= 0) servoPos[i]   = (uint16_t)pos;
    if (spdOk)   servoSpeed[i]  = (uint16_t)spdBuf[0] | ((uint16_t)spdBuf[1] << 8);
  }

  // Round-robin: one servo's temp+volt per frame.
  // AX-12A address 42 = Present Voltage (raw × 0.1 = V).
  // ControlTableItem::PRESENT_INPUT_VOLTAGE does not map correctly for AX-12A.
  if (servoAvail[slowPollIdx]) {
    uint8_t id      = SERVO_IDS[slowPollIdx];
    int32_t tmp     = dxl.readControlTableItem(ControlTableItem::PRESENT_TEMPERATURE, id);
    uint8_t voltBuf = 0;
    bool    voltOk  = dxl.read(id, 42, 1, &voltBuf, 1);
    if (tmp   >= 0) servoTemp[slowPollIdx] = (uint8_t)tmp;
    if (voltOk)     servoVolt[slowPollIdx] = voltBuf;
  }
  slowPollIdx = (slowPollIdx + 1) % NUM_SERVOS;
}

// Build and transmit a STATE frame.
static void sendStateFrame(uint8_t seq) {
  memset(stateBuf, 0, STATE_FRAME_SIZE);

  stateBuf[0] = FRAME_START;
  stateBuf[1] = FRAME_TYPE_STATE;
  stateBuf[2] = seq;
  putU32LE(stateBuf + STATE_OFF_TS, millis());
  stateBuf[STATE_OFF_ESTOP] = eStopActive ? 1 : 0;
  // bytes STATE_OFF_IMU..71 : IMU reserved (already zeroed)

  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    uint8_t* s = stateBuf + STATE_OFF_SERVOS + i * STATE_SERVO_STRIDE;
    s[0] = servoAvail[i] ? 1 : 0;
    if (!servoAvail[i]) continue;
    s[1] = servoMode[i];
    s[2] = servoPos[i]   & 0xFF;
    s[3] = servoPos[i]   >> 8;
    s[4] = servoSpeed[i] & 0xFF;
    s[5] = servoSpeed[i] >> 8;
    s[6] = servoTemp[i];
    s[7] = servoVolt[i];
  }
  // bytes 136..199 : payload (already zeroed)

  uint16_t crc = crc16(stateBuf, STATE_FRAME_SIZE - 2);
  stateBuf[STATE_FRAME_SIZE - 2] = (crc >> 8) & 0xFF;
  stateBuf[STATE_FRAME_SIZE - 1] =  crc & 0xFF;

  USB_SERIAL.write(stateBuf, STATE_FRAME_SIZE);
}

// Apply servo targets from a validated CMD frame, then reply with STATE frame.
static void processCmdFrame(const uint8_t* buf) {
  uint8_t seq = buf[CMD_OFF_SEQ];

  lastCmdFrameMs   = millis();
  binaryModeActive = true;
  eStopActive      = false;

  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    const uint8_t* s   = buf + CMD_OFF_SERVOS + i * CMD_SERVO_STRIDE;
    uint8_t  mode       = s[0];
    uint8_t  enTorque   = s[1];
    uint16_t target     = getU16LE(s + 2);
    uint8_t  id         = SERVO_IDS[i];

    if (!servoAvail[i]) continue;

    // Switch physical mode if it does not match the requested mode.
    // Must happen before torqueOn — AX-12A requires torque off to change angle limits.
    if (mode != servoMode[i]) {
      dxl.torqueOff(id);
      if (mode == 1) {
        // Switch to WHEEL: both angle limits = 0
        dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, 0);
        dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, 0);
      } else {
        // Switch to JOINT: CW=0, CCW=1023
        dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, 0);
        dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, 1023);
      }
      servoMode[i] = mode;
    }

    if (!enTorque) {
      dxl.torqueOff(id);
      continue;
    }

    dxl.torqueOn(id);

    if (mode == 1) {
      // WHEEL: target is moving speed (0–2047)
      dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, target);
    } else {
      // JOINT: target is goal position in ticks (0–1023)
      dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, target);
    }
  }

  refreshServoState();
  sendStateFrame(seq);
}

// ── Text mode helpers ─────────────────────────────────────────────────────────

String getMode(uint8_t id) {
  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  return (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";
}

void waitForMotion(uint8_t id, uint32_t timeoutMs = 3000) {
  delay(300);
  uint32_t deadline = millis() + timeoutMs;
  while (millis() < deadline) {
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

// ── Text command handlers ─────────────────────────────────────────────────────

void cmdPing(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,PING,INVALID_ID"); return; }
  if (dxl.ping(id)) {
    USB_SERIAL.print("OK,PING,"); USB_SERIAL.print(id);
    USB_SERIAL.print(",");       USB_SERIAL.println(dxl.getModelNumber(id));
  } else {
    USB_SERIAL.print("ERR,PING,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND");
  }
}

void cmdScan(const char* args) {
  uint8_t pos = 0;
  int32_t maxId = tokenInt(args, pos, 252);
  if (maxId < 1) maxId = 1;
  if (maxId > 252) maxId = 252;
  uint8_t count = 0;
  for (int id = 1; id <= maxId; id++) {
    if (dxl.ping(id)) {
      count++;
      USB_SERIAL.print("FOUND,"); USB_SERIAL.print(id);
      USB_SERIAL.print(","); USB_SERIAL.print(dxl.getModelNumber(id));
      USB_SERIAL.print(",");
      USB_SERIAL.print(dxl.readControlTableItem(ControlTableItem::FIRMWARE_VERSION, id));
      USB_SERIAL.print(","); USB_SERIAL.println(getMode(id));
    }
  }
  USB_SERIAL.print("OK,SCAN,"); USB_SERIAL.println(count);
}

void cmdMonitor(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  int32_t intervalMs = tokenInt(args, pos, 200);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,MONITOR,INVALID_ID"); return; }
  if (!dxl.ping(id)) {
    USB_SERIAL.print("ERR,MONITOR,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND");
    return;
  }
  USB_SERIAL.print("# MONITOR,START,"); USB_SERIAL.print(id);
  USB_SERIAL.print(","); USB_SERIAL.print(intervalMs); USB_SERIAL.println("ms");
  USB_SERIAL.println("# FORMAT: STATUS,ms,id,mode,pos_deg,speed_rpm,load_pct,voltage_V,temp_C");
  while (USB_SERIAL.available()) USB_SERIAL.read();
  uint32_t t0 = millis();
  while (true) {
    if (USB_SERIAL.available()) { while (USB_SERIAL.available()) USB_SERIAL.read(); break; }
    uint32_t ts    = millis() - t0;
    int32_t posVal = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
    uint8_t spdBuf2[2] = {0, 0};
    dxl.read(id, 38, 2, spdBuf2, sizeof(spdBuf2));
    int32_t spd = (int32_t)((uint16_t)spdBuf2[0] | ((uint16_t)spdBuf2[1] << 8));
    int32_t load   = dxl.readControlTableItem(ControlTableItem::PRESENT_LOAD, id);
    int32_t volt   = dxl.readControlTableItem(ControlTableItem::PRESENT_INPUT_VOLTAGE, id);
    int32_t temp   = dxl.readControlTableItem(ControlTableItem::PRESENT_TEMPERATURE, id);
    if (posVal < 0) { USB_SERIAL.println("ERR,MONITOR,LOST_CONNECTION"); break; }
    String modeStr = getMode(id);
    // PRESENT_SPEED unit differs by mode:
    //   JOINT: 0.111 rpm/tick  |  WHEEL: 0.1 %/tick (output power, not RPM)
    USB_SERIAL.print("STATUS,"); USB_SERIAL.print(ts);           USB_SERIAL.print(",");
    USB_SERIAL.print(id);        USB_SERIAL.print(",");
    USB_SERIAL.print(modeStr);  USB_SERIAL.print(",");
    USB_SERIAL.print(TICKS_TO_DEG(posVal), 2); USB_SERIAL.print(",");
    if (modeStr == "WHEEL") {
      USB_SERIAL.print(((spd & 0x3FF) * 0.1f), 1); USB_SERIAL.print("%");
    } else {
      USB_SERIAL.print(TICKS_TO_RPM(spd), 2);       USB_SERIAL.print("rpm");
    }
    USB_SERIAL.print(",");
    USB_SERIAL.print(TICKS_TO_LOAD(load), 2);  USB_SERIAL.print(",");
    USB_SERIAL.print(volt * 0.1f, 1);           USB_SERIAL.print(",");
    USB_SERIAL.println(temp);
    delay(intervalMs);
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
    USB_SERIAL.print("ERR,NUDGE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND");
    return;
  }
  uint16_t savedCw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
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
  USB_SERIAL.print("NUDGE,ORIGIN,"); USB_SERIAL.println(originDeg, 2);
  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, speed);
  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, nudgePos);
  waitForMotion(id);
  USB_SERIAL.print("NUDGE,REACHED,");
  USB_SERIAL.println(TICKS_TO_DEG(dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id)), 2);
  delay(500);
  dxl.writeControlTableItem(ControlTableItem::GOAL_POSITION, id, origin);
  waitForMotion(id);
  USB_SERIAL.print("NUDGE,RETURNED,");
  USB_SERIAL.println(TICKS_TO_DEG(dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id)), 2);
  if (wasWheel) {
    dxl.torqueOff(id);
    dxl.writeControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id, savedCw);
    dxl.writeControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id, savedCcw);
    delay(100);
  }
  USB_SERIAL.print("OK,NUDGE,"); USB_SERIAL.println(id);
}

void cmdGetPos(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETPOS,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETPOS,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  int32_t ticks = dxl.readControlTableItem(ControlTableItem::PRESENT_POSITION, id);
  USB_SERIAL.print("OK,GETPOS,"); USB_SERIAL.print(id);
  USB_SERIAL.print(",");          USB_SERIAL.print(ticks);
  USB_SERIAL.print(",");          USB_SERIAL.println(TICKS_TO_DEG(ticks), 2);
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
  USB_SERIAL.print("OK,SETPOS,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(ticks);
}

void cmdGetSpeed(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETSPEED,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETSPEED,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  uint8_t spdBuf[2] = {0, 0};
  dxl.read(id, 38, 2, spdBuf, sizeof(spdBuf));
  int32_t spd = (int32_t)((uint16_t)spdBuf[0] | ((uint16_t)spdBuf[1] << 8));
  USB_SERIAL.print("OK,GETSPEED,"); USB_SERIAL.print(id);
  USB_SERIAL.print(",");            USB_SERIAL.print(spd);
  USB_SERIAL.print(",");            USB_SERIAL.println(TICKS_TO_RPM(spd), 2);
}

void cmdSetSpeed(const char* args) {
  uint8_t pos = 0;
  int32_t id    = tokenInt(args, pos, -1);
  int32_t speed = tokenInt(args, pos, -1);
  if (id < 1 || id > 252)        { USB_SERIAL.println("ERR,SETSPEED,INVALID_ID"); return; }
  if (speed < 0 || speed > 2047) { USB_SERIAL.println("ERR,SETSPEED,INVALID_SPEED"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,SETSPEED,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  dxl.writeControlTableItem(ControlTableItem::MOVING_SPEED, id, speed);
  USB_SERIAL.print("OK,SETSPEED,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(speed);
}

void cmdGetMode(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,GETMODE,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,GETMODE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  String mode = (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";
  USB_SERIAL.print("OK,GETMODE,"); USB_SERIAL.print(id);   USB_SERIAL.print(",");
  USB_SERIAL.print(mode);           USB_SERIAL.print(",");
  USB_SERIAL.print(cw);             USB_SERIAL.print(","); USB_SERIAL.println(ccw);
}

void cmdSetMode(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  String mode = nextToken(args, pos);
  mode.toUpperCase();
  if (id < 1 || id > 252)                    { USB_SERIAL.println("ERR,SETMODE,INVALID_ID"); return; }
  if (mode != "JOINT" && mode != "WHEEL")     { USB_SERIAL.println("ERR,SETMODE,INVALID_MODE"); return; }
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
  USB_SERIAL.print("OK,SETMODE,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(mode);
}

void cmdVoltage(const char* args) {
  uint8_t pos = 0;
  int32_t id = tokenInt(args, pos, -1);
  if (id < 1 || id > 252) { USB_SERIAL.println("ERR,VOLTAGE,INVALID_ID"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,VOLTAGE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  uint8_t voltBuf = 0;
  if (!dxl.read(id, 42, 1, &voltBuf, 1)) {
    USB_SERIAL.print("ERR,VOLTAGE,"); USB_SERIAL.print(id); USB_SERIAL.println(",READ_FAILED"); return;
  }
  USB_SERIAL.print("OK,VOLTAGE,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(voltBuf * 0.1f, 1);
}

void cmdVoltages(const char* args) {
  uint8_t pos = 0;
  int32_t maxId = tokenInt(args, pos, 252);
  if (maxId < 1) maxId = 1;
  if (maxId > 252) maxId = 252;
  uint8_t count = 0;
  for (int id = 1; id <= maxId; id++) {
    if (dxl.ping(id)) {
      count++;
      uint8_t voltBuf = 0;
      dxl.read(id, 42, 1, &voltBuf, 1);
      USB_SERIAL.print("VOLTAGE,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(voltBuf * 0.1f, 1);
    }
  }
  USB_SERIAL.print("OK,VOLTAGES,"); USB_SERIAL.println(count);
}

void cmdIdChange(const char* args) {
  uint8_t pos = 0;
  int32_t currentId = tokenInt(args, pos, -1);
  int32_t newId     = tokenInt(args, pos, -1);
  if (currentId < 1 || currentId > 252) { USB_SERIAL.println("ERR,IDCHANGE,INVALID_CURRENT_ID"); return; }
  if (newId < 1 || newId > 252)         { USB_SERIAL.println("ERR,IDCHANGE,INVALID_NEW_ID"); return; }
  if (currentId == newId)               { USB_SERIAL.println("ERR,IDCHANGE,SAME_ID"); return; }
  if (!dxl.ping(currentId)) {
    USB_SERIAL.print("ERR,IDCHANGE,"); USB_SERIAL.print(currentId); USB_SERIAL.println(",NOT_FOUND"); return;
  }
  if (dxl.ping(newId)) {
    USB_SERIAL.print("ERR,IDCHANGE,"); USB_SERIAL.print(newId); USB_SERIAL.println(",CONFLICT"); return;
  }
  dxl.torqueOff(currentId);
  if (!dxl.writeControlTableItem(ControlTableItem::ID, currentId, newId)) {
    USB_SERIAL.println("ERR,IDCHANGE,WRITE_FAILED"); return;
  }
  delay(300);
  if (dxl.ping(newId)) {
    USB_SERIAL.print("OK,IDCHANGE,"); USB_SERIAL.print(currentId); USB_SERIAL.print(","); USB_SERIAL.println(newId);
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
  USB_SERIAL.print("OK,SETBAUD,"); USB_SERIAL.println(dxlBaud);
}

void cmdTorque(const char* args) {
  uint8_t pos = 0;
  int32_t id  = tokenInt(args, pos, -1);
  int32_t val = tokenInt(args, pos, -1);
  if (id < 1 || id > 252)   { USB_SERIAL.println("ERR,TORQUE,INVALID_ID"); return; }
  if (val != 0 && val != 1) { USB_SERIAL.println("ERR,TORQUE,INVALID_VALUE"); return; }
  if (!dxl.ping(id)) { USB_SERIAL.print("ERR,TORQUE,"); USB_SERIAL.print(id); USB_SERIAL.println(",NOT_FOUND"); return; }
  if (val == 1) dxl.torqueOn(id); else dxl.torqueOff(id);
  USB_SERIAL.print("OK,TORQUE,"); USB_SERIAL.print(id); USB_SERIAL.print(","); USB_SERIAL.println(val);
}

void cmdHelp() {
  USB_SERIAL.println("=== dxl_commander commands ===");
  USB_SERIAL.println("PING <id>                     Ping servo");
  USB_SERIAL.println("SCAN [max_id]                 Scan for servos (default: 1-252)");
  USB_SERIAL.println("MONITOR <id> [interval_ms]    Stream servo state CSV (any char to stop)");
  USB_SERIAL.println("NUDGE <id> [deg] [speed]      Nudge +deg and return (default: 5.0, 200)");
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
  USB_SERIAL.println("--- Binary frames: send 0xAA-prefixed CMD frame (105 bytes) ---");
}

// ── Text command dispatch ─────────────────────────────────────────────────────

void processCommand(const char* line) {
  while (*line == ' ' || *line == '\t') line++;
  if (*line == '\0' || *line == '#') return;
  uint8_t pos = 0;
  String cmd = nextToken(line, pos);
  cmd.toUpperCase();
  const char* args = line + pos;
  if      (cmd == "PING")     cmdPing(args);
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
  else { USB_SERIAL.print("ERR,UNKNOWN_CMD,"); USB_SERIAL.println(cmd); }
}

// ── Setup & Loop ─────────────────────────────────────────────────────────────

void setup() {
  pinMode(BOARD_LED_PIN, OUTPUT);
  digitalWrite(BOARD_LED_PIN, HIGH);  // off initially

  USB_SERIAL.begin(USB_BAUD);
  while (!USB_SERIAL) delay(10);

  dxl.begin(dxlBaud);
  dxl.setPortProtocolVersion(DXL_PROTOCOL);

  memset(servoAvail, 0, sizeof(servoAvail));
  memset(servoMode,  0, sizeof(servoMode));
  memset(servoPos,   0, sizeof(servoPos));
  memset(servoSpeed, 0, sizeof(servoSpeed));
  memset(servoTemp,  0, sizeof(servoTemp));
  memset(servoVolt,  0, sizeof(servoVolt));

  scanServos();

  USB_SERIAL.println("# dxl_commander ready (text + binary mode)");
  USB_SERIAL.print("# DXL baud: "); USB_SERIAL.println(dxlBaud);
  USB_SERIAL.print("# Servos found: ");
  for (uint8_t i = 0; i < NUM_SERVOS; i++) {
    if (servoAvail[i]) { USB_SERIAL.print(SERVO_IDS[i]); USB_SERIAL.print(" "); }
  }
  USB_SERIAL.println();
  USB_SERIAL.println("# Send HELP for text commands | send 0xAA CMD frame (105 bytes) for binary mode");
}

void loop() {
  updateHeartbeatLed();

  // ── Firmware watchdog ──────────────────────────────────────────────────────
  // Fires only after the first valid CMD frame has been received (binaryModeActive).
  // Zeroes all WHEEL servo speeds if the host goes silent for WATCHDOG_MS.
  if (binaryModeActive && !eStopActive) {
    if ((millis() - lastCmdFrameMs) > WATCHDOG_MS) {
      eStop();
      USB_SERIAL.println("# WATCHDOG: e-stop fired");
    }
  }

  // ── Read incoming bytes ────────────────────────────────────────────────────
  while (USB_SERIAL.available()) {
    uint8_t firstByte = (uint8_t)USB_SERIAL.peek();

    if (firstByte == FRAME_START) {
      // ── Binary frame path ─────────────────────────────────────────────────
      USB_SERIAL.read();  // consume START_BYTE
      cmdFrameBuf[0] = FRAME_START;
      if (readBinaryFrame(cmdFrameBuf, CMD_FRAME_SIZE)) {
        if (cmdFrameBuf[1] == FRAME_TYPE_CMD) {
          processCmdFrame(cmdFrameBuf);
        }
      }
      inputPos = 0;  // discard any partial text accumulation
    } else {
      // ── Text command path ─────────────────────────────────────────────────
      char c = (char)USB_SERIAL.read();
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
}

#include <Arduino.h>
#include <Dynamixel2Arduino.h>

// ================================= SERIAL ==============================================
// ── SERIAL: Constants ──────────────────────────────────────────────────────────────────

static const uint8_t  START_BYTE    = 0xAA;
static const uint8_t  CMD_HEARTBEAT = 0x01;
static const uint8_t  CMD_HB_ACK   = 0x02;
static const uint8_t  PACKET_SIZE  = 9; //TODO: Adjust the package size, to match the real data stream
static const uint8_t  PAYLOAD_SIZE = 4;
static const uint32_t SERIAL_BAUD_RATE    = 115200;
static const uint32_t LED_INTERVAL = 500;   // ms — blink period when comms OK
static const uint32_t COMM_TIMEOUT = 1000;   // ms — if no hearbeat, declare comms lost

// ── SERIAL: Packet struct (all uint8_t fields — zero padding risk) ────────────────────

struct Packet {
    uint8_t start; // TODO: instead of calling it start, it would be nice to call it header instead. start is misleading.
    uint8_t cmd; //TODO: Make sure the commands are enumerated. Don't create too much cmd (e.g. servo monitoring data should be stream without needing for commands as request). It is better to focus commands on robot actions like setting servo's current position or current speed.
    uint8_t seq; //TODO: At the moment, this size is too small for a sequence. It would be nice to have bigger data type.
    //TODO: time in milliseconds
    uint8_t payload[PAYLOAD_SIZE]; //TODO: this makes the struct to have too many overheads. I need to think if this is needed, or simply put header, cmd and seq, into a unified data structure would be better.
    uint8_t crc_hi;
    uint8_t crc_lo;
};


// ── SERIAL: State ─────────────────────────────────────────────────────────────────────

static uint8_t  tx_seq    = 0;
static uint32_t last_hb_ms = 0;     // millis() timestamp of last valid heartbeat (0 = never received)
static bool     led_state = false;
static uint32_t led_last  = 0;

// Compile-time check: struct must be exactly PACKET_SIZE bytes.
static_assert(sizeof(Packet) == PACKET_SIZE, "Packet size mismatch — check for padding");

// ── CRC-16 CCITT (poly=0x1021, init=0xFFFF) ───────────────────────────────────

static uint16_t crc16(const uint8_t* data, uint8_t len) {
    uint16_t crc = 0xFFFF;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++) {
            crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
        }
    }
    return crc;
}

// ── Packet I/O ─────────────────────────────────────────────────────────────────

// Build and write a packet to Serial.
static void sendPacket(uint8_t cmd, uint8_t seq) {
    Packet pkt;
    pkt.start = START_BYTE;
    pkt.cmd   = cmd;
    pkt.seq   = seq;
    memset(pkt.payload, 0, PAYLOAD_SIZE);

    uint16_t crc = crc16((uint8_t*)&pkt, PACKET_SIZE - 2);
    pkt.crc_hi = (crc >> 8) & 0xFF;
    pkt.crc_lo =  crc        & 0xFF;

    Serial.write((uint8_t*)&pkt, PACKET_SIZE);
}

// Non-blocking: reads one byte at a time from Serial.
// Returns true (and fills *out) when a complete, CRC-valid packet arrives.
// Re-syncs automatically: buffering only starts on START_BYTE.
static bool readPacket(Packet* out) {
    static uint8_t buf[PACKET_SIZE];
    static uint8_t idx = 0;

    while (Serial.available()) {
        uint8_t b = (uint8_t)Serial.read();

        // Re-sync: drop bytes until we see START_BYTE at position 0.
        if (idx == 0 && b != START_BYTE) continue;

        buf[idx++] = b;

        if (idx == PACKET_SIZE) {
            idx = 0;
            uint16_t expected = crc16(buf, PACKET_SIZE - 2);
            uint16_t received = ((uint16_t)buf[7] << 8) | buf[8];

            if (expected == received) {
                memcpy(out, buf, PACKET_SIZE);
                return true;
            }
            // CRC mismatch — silently discard and wait for next packet.
        }
    }
    return false;
}

// ── Non-blocking LED blink ────────────────────────────────────────────────────

static void updateLed() {
    uint32_t now = millis();

    // Communication is healthy only if a heartbeat arrived within COMM_TIMEOUT.
    // last_hb_ms == 0 means no heartbeat has ever been received.
    bool comm_ok = (last_hb_ms != 0) && (now - last_hb_ms < COMM_TIMEOUT);

    if (now - led_last >= LED_INTERVAL) {
        led_last  = now;
        led_state = !led_state;
        digitalWrite(LED_BUILTIN, led_state ? HIGH : LOW);
    }

    if (!comm_ok) {
        digitalWrite(LED_BUILTIN, HIGH);
        led_state = false;
        return;
    }
}

//================================= DXL ==============================================
// ── DXL: User configuration ────────────────────────────────────────────────────────
#define SERVO_ID     1
#define BAUD_RATE    1000000
#define UPDATE_MS    200     // Stream interval in milliseconds
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

String getMode(int id) {
  uint16_t cw  = dxl.readControlTableItem(ControlTableItem::CW_ANGLE_LIMIT,  id);
  uint16_t ccw = dxl.readControlTableItem(ControlTableItem::CCW_ANGLE_LIMIT, id);
  return (cw == 0 && ccw == 0) ? "WHEEL" : "JOINT";
}


//================================= MAIN ============================================
// ── Arduino entry points ──────────────────────────────────────────────────────────
void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);
    Serial.begin(SERIAL_BAUD_RATE);

    dxl.begin(BAUD_RATE);
    dxl.setPortProtocolVersion(DXL_PROTOCOL);

    DEBUG_SERIAL.print("# Mode    : "); DEBUG_SERIAL.println(getMode());
    DEBUG_SERIAL.println("#");

}

void loop() {
    Packet pkt;

    if (readPacket(&pkt)) {
        if (pkt.cmd == CMD_HEARTBEAT) {
	    // TODO: Create a package that sends mode of each servo ID from 1 to 8
            sendPacket(CMD_HB_ACK, tx_seq++);
            last_hb_ms = millis();
        }
    }

    updateLed();
}

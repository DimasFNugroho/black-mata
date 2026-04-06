// serial_rw_example.ino
//
// Binary serial communication example — OpenCM9.04 side.
//
// Flow:
//   1. Jetson sends a HEARTBEAT packet every N seconds.
//   2. MCU validates the packet (CRC-16 CCITT).
//   3. If valid, MCU sends HB_ACK and blinks the onboard LED at 1 Hz.
//
// Packet layout (9 bytes, fixed length, no padding):
//   [0]    START   = 0xAA
//   [1]    CMD     uint8  (0x01 = HEARTBEAT, 0x02 = HB_ACK)
//   [2]    SEQ     uint8  (rolling 0-255)
//   [3..6] PAYLOAD uint8[4]  (reserved, zeros)
//   [7]    CRC_HI  uint8
//   [8]    CRC_LO  uint8
//
// CRC-16 CCITT (poly=0x1021, init=0xFFFF) computed over bytes [0..6].

#include <Arduino.h>

// ── Constants ──────────────────────────────────────────────────────────────────

static const uint8_t  START_BYTE    = 0xAA;
static const uint8_t  CMD_HEARTBEAT = 0x01;
static const uint8_t  CMD_HB_ACK   = 0x02;
static const uint8_t  PACKET_SIZE  = 9;
static const uint8_t  PAYLOAD_SIZE = 4;
static const uint32_t BAUD_RATE    = 115200;
static const uint32_t LED_INTERVAL = 1000;   // ms — blink period when comms OK

// ── Packet struct (all uint8_t fields — zero padding risk) ────────────────────

struct Packet {
    uint8_t start;
    uint8_t cmd;
    uint8_t seq;
    uint8_t payload[PAYLOAD_SIZE];
    uint8_t crc_hi;
    uint8_t crc_lo;
};

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

// ── State ─────────────────────────────────────────────────────────────────────

static uint8_t  tx_seq    = 0;
static bool     comm_ok   = false;   // true after first valid heartbeat
static bool     led_state = false;
static uint32_t led_last  = 0;

// ── Non-blocking LED blink ────────────────────────────────────────────────────

static void updateLed() {
    if (!comm_ok) {
        // LED off when no communication has been established yet.
        digitalWrite(LED_BUILTIN, LOW);
        led_state = false;
        return;
    }
    uint32_t now = millis();
    if (now - led_last >= LED_INTERVAL) {
        led_last  = now;
        led_state = !led_state;
        digitalWrite(LED_BUILTIN, led_state ? HIGH : LOW);
    }
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LOW);
    Serial.begin(BAUD_RATE);
}

void loop() {
    Packet pkt;

    if (readPacket(&pkt)) {
        if (pkt.cmd == CMD_HEARTBEAT) {
            sendPacket(CMD_HB_ACK, tx_seq++);
            comm_ok = true;
        }
    }

    updateLed();
}

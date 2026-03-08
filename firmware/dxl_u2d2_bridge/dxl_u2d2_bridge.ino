/**
 * dxl_u2d2_bridge.ino
 *
 * Makes OpenCM9.04 behave like a U2D2: a transparent USB-to-Dynamixel
 * bridge. The Jetson sends raw Dynamixel protocol packets over USB
 * Serial, and the OpenCM forwards them to the servo bus and returns
 * the servo response.
 *
 * Works with Protocol 1.0 (AX series) and Protocol 2.0 (X series).
 * No packet parsing — uses a timeout to detect end of packet and
 * switches half-duplex direction accordingly.
 *
 * Wiring:
 *   OpenCM9.04 3-pin TTL Dynamixel connector → servo chain
 *   OpenCM9.04 USB → Jetson USB
 *
 * Jetson setup (DynamixelSDK):
 *   - Open the port that appears when OpenCM is connected (e.g. /dev/ttyACM0)
 *   - Set baud rate to BAUD_RATE (default 1000000)
 *   - Use Protocol 1.0 for AX-12A, Protocol 2.0 for X series
 *
 * Timing parameters:
 *   PACKET_END_TIMEOUT_US — how long to wait with no incoming USB byte
 *                           before assuming the instruction packet is
 *                           complete and switching to RX mode.
 *                           At 1Mbps a byte takes ~10µs. USB CDC batching
 *                           can add up to ~1ms. 2ms is a safe margin.
 *
 *   RESPONSE_TIMEOUT_MS   — how long to wait for servo response bytes
 *                           after switching to RX. Resets on each byte
 *                           received to handle multi-byte responses.
 *                           AX-12A typically responds in <1ms at 1Mbps.
 */

#define USB_SERIAL    Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28

#define BAUD_RATE               1000000UL

// Tune these if you experience dropped bytes or missed responses
#define PACKET_END_TIMEOUT_US   2000    // 2ms
#define RESPONSE_TIMEOUT_MS     10      // 10ms

// ─────────────────────────────────────────────────────────────────────────────

inline void dirTX() { digitalWrite(DXL_DIR_PIN, HIGH); }
inline void dirRX() { digitalWrite(DXL_DIR_PIN, LOW);  }

void setup() {
  pinMode(DXL_DIR_PIN, OUTPUT);
  dirRX();

  USB_SERIAL.begin(BAUD_RATE);
  DXL_SERIAL.begin(BAUD_RATE);
}

void loop() {
  if (!USB_SERIAL.available()) return;

  // ── TX phase: forward instruction packet to servo bus ──────────────────────
  dirTX();

  while (true) {
    if (USB_SERIAL.available()) {
      DXL_SERIAL.write(USB_SERIAL.read());
    } else {
      // Wait briefly for more bytes before declaring packet complete
      uint32_t t = micros();
      while (!USB_SERIAL.available() && (micros() - t) < PACKET_END_TIMEOUT_US);
      if (!USB_SERIAL.available()) break;
    }
  }

  // Wait for the hardware TX buffer to fully drain before switching direction
  DXL_SERIAL.flush();
  dirRX();

  // ── RX phase: collect servo response and forward to Jetson ─────────────────
  uint32_t deadline = millis() + RESPONSE_TIMEOUT_MS;
  while (millis() < deadline) {
    if (DXL_SERIAL.available()) {
      USB_SERIAL.write(DXL_SERIAL.read());
      deadline = millis() + RESPONSE_TIMEOUT_MS; // extend on each byte
    }
  }
}

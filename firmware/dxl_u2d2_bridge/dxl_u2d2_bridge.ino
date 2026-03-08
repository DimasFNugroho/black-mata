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
 * Notes:
 *   Serial1.setDxlMode(true) configures the OpenCM9.04 USART1 for
 *   Dynamixel half-duplex operation — this is required to prevent the
 *   UART from echoing TX bytes back through RX (same approach used
 *   internally by the Dynamixel2Arduino library).
 *
 *   The RX buffer is cleared BEFORE each TX packet, matching the
 *   behavior of the official DynamixelSDK port handler (clearPort).
 */

#define USB_SERIAL    Serial
#define DXL_SERIAL    Serial1
#define DXL_DIR_PIN   28

#define BAUD_RATE               1000000UL

#define PACKET_END_TIMEOUT_US   2000    // 2ms — wait for more USB bytes
#define RESPONSE_TIMEOUT_MS     10      // 10ms — wait for servo response

// ─────────────────────────────────────────────────────────────────────────────

inline void dirTX() { digitalWrite(DXL_DIR_PIN, HIGH); }
inline void dirRX() { digitalWrite(DXL_DIR_PIN, LOW);  }

void setup() {
  pinMode(DXL_DIR_PIN, OUTPUT);
  dirRX();

  USB_SERIAL.begin(BAUD_RATE);

  // setDxlMode(true) configures USART1 for Dynamixel half-duplex —
  // prevents TX echo from appearing in the RX buffer
  DXL_SERIAL.begin(BAUD_RATE);
  DXL_SERIAL.setDxlMode(true);
}

void loop() {
  if (!USB_SERIAL.available()) return;

  // Clear any stale bytes in the DXL RX buffer before sending
  // (mirrors clearPort() in the official DynamixelSDK port handler)
  while (DXL_SERIAL.available()) DXL_SERIAL.read();

  // ── TX phase: forward instruction packet to servo bus ──────────────────────
  dirTX();

  while (true) {
    if (USB_SERIAL.available()) {
      DXL_SERIAL.write(USB_SERIAL.read());
    } else {
      uint32_t t = micros();
      while (!USB_SERIAL.available() && (micros() - t) < PACKET_END_TIMEOUT_US);
      if (!USB_SERIAL.available()) break;
    }
  }

  DXL_SERIAL.flush();
  dirRX();

  // ── RX phase: collect servo response and forward to Jetson ─────────────────
  uint32_t deadline = millis() + RESPONSE_TIMEOUT_MS;
  while (millis() < deadline) {
    if (DXL_SERIAL.available()) {
      USB_SERIAL.write(DXL_SERIAL.read());
      deadline = millis() + RESPONSE_TIMEOUT_MS;
    }
  }
}

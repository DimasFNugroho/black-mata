/**
 * spi_scanner.ino
 *
 * Scans SPI1 on OpenCM9.04 for responsive devices by toggling each
 * candidate CS pin and checking for a non-0xFF / non-0x00 response byte.
 *
 * SPI1 pins (OpenCM9.04):
 *   SCK  → Pin 1
 *   MISO → Pin 6
 *   MOSI → Pin 7
 *
 * CS pins probed: all digital pins except SPI bus pins and known
 * reserved pins (serial, LED).
 *
 * How it works:
 *   For each candidate CS pin, the scanner:
 *     1. Pulls CS LOW (selects device)
 *     2. Sends a 0x00 byte and reads the response
 *     3. Pulls CS HIGH (deselects device)
 *   A device is considered present if the response differs from both
 *   0x00 (bus shorted low) and 0xFF (floating/no device).
 *
 * Note: SPI scanning is not as reliable as I2C scanning because SPI
 * has no standard device-address protocol. A device may still respond
 * with 0xFF or 0x00 even when present. If a known device is not
 * detected, try running the scan with that device's actual CS pin
 * manually and check the wiring.
 *
 * Output: 115200 baud USB Serial.
 */

#include <SPI.h>

#define SPI_CLOCK   1000000   // 1 MHz — conservative for scanning
#define SPI_MODE    SPI_MODE3 // BNO080 uses Mode 3; adjust if needed

// SPI1 bus pins — skip these as CS candidates
const uint8_t SPI_SCK  = 1;
const uint8_t SPI_MISO = 6;
const uint8_t SPI_MOSI = 7;

// Pins to skip entirely (reserved/special on OpenCM9.04)
// 14 = LED, 0 = tested as CS below, serial pins handled by exclusion
const uint8_t SKIP_PINS[] = { SPI_SCK, SPI_MISO, SPI_MOSI };

// CS pins to probe — adjust range to match your board's available pins
const uint8_t CS_CANDIDATES[] = { 0, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 15, 16, 17 };
const uint8_t NUM_CANDIDATES  = sizeof(CS_CANDIDATES) / sizeof(CS_CANDIDATES[0]);

bool shouldSkip(uint8_t pin) {
  for (uint8_t i = 0; i < sizeof(SKIP_PINS); i++) {
    if (SKIP_PINS[i] == pin) return true;
  }
  return false;
}

uint8_t probeCS(uint8_t csPin) {
  pinMode(csPin, OUTPUT);
  digitalWrite(csPin, HIGH);
  delay(5);

  SPI.beginTransaction(SPISettings(SPI_CLOCK, MSBFIRST, SPI_MODE));
  digitalWrite(csPin, LOW);
  delayMicroseconds(10);
  uint8_t response = SPI.transfer(0x00);
  digitalWrite(csPin, HIGH);
  SPI.endTransaction();

  return response;
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  SPI.begin();

  Serial.println("==============================================");
  Serial.println(" SPI1 Device Scanner — OpenCM9.04");
  Serial.println(" SCK=1  MISO=6  MOSI=7");
  Serial.println(" SPI Mode 3 | Clock 1 MHz");
  Serial.println("==============================================");
  Serial.println();
  Serial.println("Probing CS pins...");
  Serial.println();

  uint8_t found = 0;

  for (uint8_t i = 0; i < NUM_CANDIDATES; i++) {
    uint8_t pin = CS_CANDIDATES[i];

    if (shouldSkip(pin)) continue;

    uint8_t resp = probeCS(pin);

    Serial.print("  CS pin ");
    if (pin < 10) Serial.print(" ");
    Serial.print(pin);
    Serial.print("  →  0x");
    if (resp < 0x10) Serial.print("0");
    Serial.print(resp, HEX);

    if (resp != 0xFF && resp != 0x00) {
      Serial.println("  *** DEVICE DETECTED ***");
      found++;
    } else if (resp == 0x00) {
      Serial.println("  (bus low / shorted?)");
    } else {
      Serial.println("  (no response)");
    }

    delay(10);
  }

  Serial.println();
  Serial.print("Scan complete. Devices detected: ");
  Serial.println(found);
  Serial.println();
  Serial.println("Note: some devices respond with 0xFF or 0x00 even");
  Serial.println("when present. Check wiring if expected device missing.");
}

void loop() {
  // Re-scan every 5 seconds
  delay(5000);

  Serial.println("----------------------------------------------");
  Serial.println("Re-scanning...");
  Serial.println();

  uint8_t found = 0;

  for (uint8_t i = 0; i < NUM_CANDIDATES; i++) {
    uint8_t pin = CS_CANDIDATES[i];
    if (shouldSkip(pin)) continue;

    uint8_t resp = probeCS(pin);

    Serial.print("  CS pin ");
    if (pin < 10) Serial.print(" ");
    Serial.print(pin);
    Serial.print("  →  0x");
    if (resp < 0x10) Serial.print("0");
    Serial.print(resp, HEX);

    if (resp != 0xFF && resp != 0x00) {
      Serial.println("  *** DEVICE DETECTED ***");
      found++;
    } else if (resp == 0x00) {
      Serial.println("  (bus low / shorted?)");
    } else {
      Serial.println("  (no response)");
    }

    delay(10);
  }

  Serial.println();
  Serial.print("Devices detected: ");
  Serial.println(found);
}

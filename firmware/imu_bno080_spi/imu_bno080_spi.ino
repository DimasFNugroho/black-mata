/**
 * imu_bno080_spi.ino
 *
 * Reads all sensor reports from a BNO080 IMU over SPI1 on OpenCM9.04
 * and streams them as CSV lines over USB Serial to the ARM host.
 *
 * SPI1 wiring (OpenCM9.04):
 *   CS   → Pin 0
 *   SCK  → Pin 1
 *   MISO → Pin 6
 *   MOSI → Pin 7
 *   INT  → Pin 13
 *   RST  → Pin 14
 *   WAK  → Pin 15
 *
 * Library required:
 *   SparkFun BNO080 Cortex Based IMU
 *   Install via: arduino-cli lib install "SparkFun BNO080 Cortex Based IMU"
 *
 * Output format (CSV over Serial, 115200 baud):
 *   QUAT,<ms>,<i>,<j>,<k>,<real>,<rad_accuracy>
 *   ACCEL,<ms>,<x>,<y>,<z>          (m/s^2)
 *   GYRO,<ms>,<x>,<y>,<z>           (rad/s)
 *   LINACC,<ms>,<x>,<y>,<z>         (m/s^2, gravity removed)
 *   GRAV,<ms>,<x>,<y>,<z>           (m/s^2)
 *   MAG,<ms>,<x>,<y>,<z>            (uTesla)
 */

#include <SPI.h>
#include "SparkFun_BNO080_Arduino_Library.h"

// SPI1 pin definitions (OpenCM9.04)
#define IMU_CS   0
#define IMU_WAK  15
#define IMU_INT  13
#define IMU_RST  14

// SPI clock (BNO080 max: 3 MHz)
#define SPI_CLOCK 3000000

// Report interval in milliseconds (50ms = 20 Hz)
#define REPORT_INTERVAL_MS 50

BNO080 imu;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  SPI.begin();

  if (!imu.beginSPI(IMU_CS, IMU_WAK, IMU_INT, IMU_RST, SPI_CLOCK, SPI)) {
    Serial.println("ERROR: BNO080 not detected. Check wiring and SPI connections.");
    while (1) delay(100);
  }

  imu.enableRotationVector(REPORT_INTERVAL_MS);
  imu.enableAccelerometer(REPORT_INTERVAL_MS);
  imu.enableGyro(REPORT_INTERVAL_MS);
  imu.enableLinearAccelerometer(REPORT_INTERVAL_MS);
  imu.enableGravity(REPORT_INTERVAL_MS);
  imu.enableMagnetometer(REPORT_INTERVAL_MS);

  Serial.println("# BNO080 IMU ready — streaming at 20 Hz");
  Serial.println("# FORMAT: TYPE,timestamp_ms,values...");
}

void loop() {
  if (!imu.dataAvailable()) return;

  uint32_t ts = millis();

  // Rotation vector (quaternion)
  Serial.print("QUAT,");
  Serial.print(ts);        Serial.print(",");
  Serial.print(imu.getQuatI(), 6);    Serial.print(",");
  Serial.print(imu.getQuatJ(), 6);    Serial.print(",");
  Serial.print(imu.getQuatK(), 6);    Serial.print(",");
  Serial.print(imu.getQuatReal(), 6); Serial.print(",");
  Serial.println(imu.getQuatRadianAccuracy(), 6);

  // Accelerometer (m/s^2)
  Serial.print("ACCEL,");
  Serial.print(ts);                   Serial.print(",");
  Serial.print(imu.getAccelX(), 4);   Serial.print(",");
  Serial.print(imu.getAccelY(), 4);   Serial.print(",");
  Serial.println(imu.getAccelZ(), 4);

  // Gyroscope (rad/s)
  Serial.print("GYRO,");
  Serial.print(ts);                   Serial.print(",");
  Serial.print(imu.getGyroX(), 4);    Serial.print(",");
  Serial.print(imu.getGyroY(), 4);    Serial.print(",");
  Serial.println(imu.getGyroZ(), 4);

  // Linear acceleration (m/s^2, gravity removed)
  Serial.print("LINACC,");
  Serial.print(ts);                       Serial.print(",");
  Serial.print(imu.getLinAccelX(), 4);    Serial.print(",");
  Serial.print(imu.getLinAccelY(), 4);    Serial.print(",");
  Serial.println(imu.getLinAccelZ(), 4);

  // Gravity vector (m/s^2)
  Serial.print("GRAV,");
  Serial.print(ts);                     Serial.print(",");
  Serial.print(imu.getGravityX(), 4);   Serial.print(",");
  Serial.print(imu.getGravityY(), 4);   Serial.print(",");
  Serial.println(imu.getGravityZ(), 4);

  // Magnetometer (uTesla)
  Serial.print("MAG,");
  Serial.print(ts);                  Serial.print(",");
  Serial.print(imu.getMagX(), 4);    Serial.print(",");
  Serial.print(imu.getMagY(), 4);    Serial.print(",");
  Serial.println(imu.getMagZ(), 4);
}

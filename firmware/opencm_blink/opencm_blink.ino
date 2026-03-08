void setup() {
  pinMode(BOARD_LED_PIN, OUTPUT);
}

void loop() {
  digitalWrite(BOARD_LED_PIN, HIGH);
  delay(1000);
  digitalWrite(BOARD_LED_PIN, LOW);
  delay(1000);
}

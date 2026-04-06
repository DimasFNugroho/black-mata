#pragma once
#include <Arduino.h>

// Non-blocking LED blinker.
// Call update() every loop iteration — it toggles the pin when the period expires.
class LedBlinker {
public:
    LedBlinker(uint8_t pin, uint32_t period_ms);
    void begin();
    void update();

private:
    uint8_t  _pin;
    uint32_t _period_ms;
    uint32_t _last_toggle_ms;
    bool     _state;
};

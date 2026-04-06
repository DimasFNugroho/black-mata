#include "LedBlinker.h"

LedBlinker::LedBlinker(uint8_t pin, uint32_t period_ms)
    : _pin(pin), _period_ms(period_ms), _last_toggle_ms(0), _state(false) {}

void LedBlinker::begin()
{
    pinMode(_pin, OUTPUT);
    digitalWrite(_pin, LOW);
}

void LedBlinker::update()
{
    uint32_t now = millis();
    if (now - _last_toggle_ms >= _period_ms / 2) {
        _state = !_state;
        digitalWrite(_pin, _state ? HIGH : LOW);
        _last_toggle_ms = now;
    }
}

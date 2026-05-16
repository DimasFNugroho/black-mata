"""
estop.py — Software-side WebSocket watchdog and e-stop coordinator.

Monitors the last time a valid drive command was received over WebSocket.
If the gap exceeds WS_TIMEOUT_S, sends a zero-speed frame to all servos
and marks the link as lost. The Robot Agent calls notify() on every valid
WebSocket message; the watchdog loop runs in a background thread.

E-stop hierarchy
----------------
  1. Firmware watchdog (500 ms) — fires if OpenCM receives no CMD frame.
     Guards against Jetson crash or serial link failure.
  2. This module (default 500 ms) — fires if the WebSocket goes silent.
     Guards against network drop or operator disconnect.

Both layers independently drive servos to zero speed, so a failure at
either level is safe without relying on the other.

Usage:
    estop = EStopWatchdog(driver, timeout_s=0.5)
    estop.start()

    # On each valid WebSocket drive message:
    estop.notify()

    # To manually trigger (e.g., HTTP /estop endpoint):
    estop.trigger("operator requested")

    # On shutdown:
    estop.stop()
"""

import threading
import time
from typing import Optional

from software.robot.serial_driver import SerialDriver


class EStopWatchdog:
    """
    Background watchdog that sends a zero-speed CMD frame when the
    WebSocket drive stream goes silent for longer than timeout_s.
    """

    def __init__(self, driver: SerialDriver, timeout_s: float = 0.5):
        self._driver    = driver
        self._timeout   = timeout_s

        self._last_cmd  = time.monotonic()  # updated by notify()
        self._active    = False             # True while e-stop is in effect
        self._lock      = threading.Lock()

        self._running   = False
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background watchdog thread."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name='estop-watchdog'
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog thread cleanly."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def notify(self) -> None:
        """
        Call this on every valid incoming drive command.
        Clears the e-stop state if it was active (link recovered).
        """
        with self._lock:
            self._last_cmd = time.monotonic()
            if self._active:
                self._active = False
                print('[EStop] Link recovered — e-stop cleared')

    def trigger(self, reason: str = 'manual') -> None:
        """
        Manually trigger an e-stop (e.g. from an HTTP endpoint or signal handler).
        Idempotent — safe to call repeatedly.
        """
        with self._lock:
            already = self._active
            self._active = True
        if not already:
            print(f'[EStop] Triggered: {reason}')
        self._send_estop()

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def seconds_since_cmd(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_cmd

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Poll every ~50 ms; fire e-stop if the link has been silent too long."""
        while self._running:
            time.sleep(0.05)

            with self._lock:
                elapsed = time.monotonic() - self._last_cmd
                already = self._active

            if elapsed > self._timeout and not already:
                with self._lock:
                    self._active = True
                print(f'[EStop] Watchdog fired after {elapsed:.2f}s silence')
                self._send_estop()

    def _send_estop(self) -> None:
        try:
            self._driver.send_estop()
        except Exception as e:
            print(f'[EStop] send_estop failed: {e}')

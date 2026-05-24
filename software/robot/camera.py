"""
camera.py — MJPEG capture loop for the Robot Agent.

Opens a V4L2 (or any OpenCV-compatible) camera device and produces a
continuous MJPEG byte stream suitable for HTTP multipart/x-mixed-replace
responses. A background thread captures frames; callers read the latest
JPEG via get_frame().

Usage:
    cam = Camera(device=0, width=640, height=480, fps=30, quality=70)
    cam.start()

    # In a FastAPI streaming response:
    async def stream():
        while True:
            jpg = cam.get_frame()
            if jpg:
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n'
            await asyncio.sleep(1 / 30)

    cam.stop()
"""

import os
import threading
import time
from typing import Optional, Union

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


class Camera:
    """
    Background MJPEG capture. Thread-safe; get_frame() never blocks.
    """

    def __init__(
        self,
        device:  Union[int, str] = '/dev/robot_camera',
        width:   int = 640,
        height:  int = 480,
        fps:     int = 30,
        quality: int = 70,
    ):
        if not _CV2_AVAILABLE:
            raise RuntimeError('opencv-python is required: pip install opencv-python-headless')

        self._device  = device
        self._width   = width
        self._height  = height
        self._fps     = fps
        self._quality = quality

        self._frame:  Optional[bytes] = None
        self._lock    = threading.Lock()

        self._cap:          Optional[cv2.VideoCapture] = None
        self._running       = False
        self._reconnecting  = False
        self._thread:       Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the camera and start the capture thread."""
        if self._running:
            return
        self._cap = cv2.VideoCapture(self._device)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS,          self._fps)
        if not self._cap.isOpened():
            raise RuntimeError(f'Cannot open camera device {self._device}')

        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name='camera-capture'
        )
        self._thread.start()
        print(f'[Camera] Started: {self._device_label()} {self._width}x{self._height} @ {self._fps} fps')

    def stop(self) -> None:
        """Stop the capture thread and release the camera."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._cap:
            self._cap.release()
            self._cap = None

    def get_frame(self) -> Optional[bytes]:
        """Return the latest JPEG frame bytes, or None if none captured yet."""
        with self._lock:
            return self._frame

    def is_healthy(self) -> bool:
        """Return True if the camera is actively capturing frames."""
        return self._running and not self._reconnecting

    # ── Internal ──────────────────────────────────────────────────────────────

    def _device_label(self) -> str:
        """Return 'device (-> /dev/videoX)' if device is a symlink, else just 'device'."""
        if isinstance(self._device, str):
            real = os.path.realpath(self._device)
            if real != self._device:
                return f'{self._device} (-> {real})'
        return str(self._device)

    def _reconnect(self) -> None:
        """Release the current capture and reopen the same device path."""
        self._reconnecting = True
        with self._lock:
            self._frame = None
        print(f'[Camera] Disconnected from {self._device_label()}. Waiting for reconnect...')
        self._cap.release()
        while self._running:
            time.sleep(2.0)
            self._cap = cv2.VideoCapture(self._device)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS,          self._fps)
            if self._cap.isOpened():
                self._reconnecting = False
                print(f'[Camera] Reconnected: {self._device_label()}')
                return
            print(f'[Camera] Not available yet, retrying...')

    def _loop(self) -> None:
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._quality]
        interval = 1.0 / self._fps
        fail_count = 0

        while self._running:
            t0 = time.monotonic()
            ret, frame = self._cap.read()
            if ret:
                fail_count = 0
                ok, buf = cv2.imencode('.jpg', frame, encode_params)
                if ok:
                    with self._lock:
                        self._frame = buf.tobytes()
            else:
                fail_count += 1
                if fail_count >= 10:
                    self._reconnect()
                    fail_count = 0

            sleep_for = interval - (time.monotonic() - t0)
            if sleep_for > 0:
                time.sleep(sleep_for)

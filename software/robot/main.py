"""
main.py — Black-Mata Robot Agent (FastAPI).

Endpoints:
  WS  /ws       Drive commands from operator dashboard → Ackermann → servos.
  GET /stream   MJPEG camera stream (multipart/x-mixed-replace).
  GET /status   Latest servo telemetry as JSON.
  POST /estop   Manual e-stop from HTTP (idempotent).

Drive message (JSON over WebSocket):
  { "steer": <float degrees, ±30>, "speed": <float m/s, ±0.5> }

Status response:
  {
    "e_stop":     bool,
    "seq":        int,
    "timestamp_ms": int,
    "servos": [
      { "id": int, "available": bool, "mode": str,
        "pos": int, "speed": int, "temp_c": int, "volt_v": float }
    ]
  }

Run:
    uvicorn software.robot.main:app --host 0.0.0.0 --port 8000

Configuration via environment variables:
    SERIAL_PORT       serial port for OpenCM  (default: /dev/opencm)
    SERIAL_BAUD       baud rate               (default: 115200)
    CAMERA_DEVICE     V4L2 device index       (default: 0)
    CAMERA_WIDTH      capture width           (default: 640)
    CAMERA_HEIGHT     capture height          (default: 480)
    CAMERA_FPS        capture frame rate      (default: 30)
    CAMERA_QUALITY    JPEG quality 1-100      (default: 70)
    WS_TIMEOUT        e-stop watchdog seconds (default: 0.5)
"""

import asyncio
import json
import os
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse

from software.robot.serial_driver import SerialDriver
from software.robot.ackermann import Ackermann, AckermannConfig
from software.robot.estop import EStopWatchdog
from software.robot.camera import Camera

# ── Configuration from environment ────────────────────────────────────────────

SERIAL_PORT    = os.getenv('SERIAL_PORT',    '/dev/opencm')
SERIAL_BAUD    = int(os.getenv('SERIAL_BAUD',    '115200'))
CAMERA_DEVICE  = int(os.getenv('CAMERA_DEVICE',  '1'))
CAMERA_WIDTH   = int(os.getenv('CAMERA_WIDTH',   '640'))
CAMERA_HEIGHT  = int(os.getenv('CAMERA_HEIGHT',  '480'))
CAMERA_FPS     = int(os.getenv('CAMERA_FPS',     '30'))
CAMERA_QUALITY = int(os.getenv('CAMERA_QUALITY', '70'))
WS_TIMEOUT     = float(os.getenv('WS_TIMEOUT',   '0.5'))

# ── Singletons ────────────────────────────────────────────────────────────────

driver  = SerialDriver(SERIAL_PORT, SERIAL_BAUD)
ack     = Ackermann(AckermannConfig())
estop   = EStopWatchdog(driver, timeout_s=WS_TIMEOUT)
camera  = Camera(CAMERA_DEVICE, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS, CAMERA_QUALITY)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title='Black-Mata Robot Agent')


@app.on_event('startup')
async def _startup():
    driver.connect()
    driver.start()
    estop.start()
    try:
        camera.start()
    except Exception as e:
        print(f'[main] Camera unavailable: {e}')


@app.on_event('shutdown')
async def _shutdown():
    estop.trigger('server shutdown')
    estop.stop()
    camera.stop()
    driver.stop()
    driver.close()


# ── WebSocket /ws ─────────────────────────────────────────────────────────────

@app.websocket('/ws')
async def ws_drive(websocket: WebSocket):
    await websocket.accept()
    print('[WS] Operator connected')
    estop.arm()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                steer = float(msg.get('steer', 0.0))
                speed = float(msg.get('speed', 0.0))
            except (ValueError, KeyError):
                await websocket.send_text('{"error": "invalid message"}')
                continue

            targets = ack.compute(steer_deg=steer, speed_mps=speed)
            driver.send_frame(targets)
            estop.notify()

    except WebSocketDisconnect:
        print('[WS] Operator disconnected')
        estop.trigger('WebSocket disconnected')


# ── GET /stream ───────────────────────────────────────────────────────────────

async def _mjpeg_generator() -> AsyncGenerator[bytes, None]:
    interval = 1.0 / CAMERA_FPS
    while True:
        jpg = camera.get_frame()
        if jpg:
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n'
                + jpg +
                b'\r\n'
            )
        await asyncio.sleep(interval)


@app.get('/stream')
async def stream():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type='multipart/x-mixed-replace; boundary=frame',
    )


# ── GET /status ───────────────────────────────────────────────────────────────

@app.get('/status')
async def status():
    state = driver.get_state()
    if state is None:
        return JSONResponse({'error': 'no state received yet'}, status_code=503)

    servos = []
    for s in state.servos:
        servos.append({
            'id':        s.servo_id,
            'available': s.available,
            'mode':      'WHEEL' if s.mode else 'JOINT',
            'pos':       s.pos,
            'speed':     s.speed,
            'temp_c':    s.temperature,
            'volt_v':    s.voltage,
        })

    return {
        'e_stop':       state.e_stop or estop.is_active,
        'seq':          state.seq,
        'timestamp_ms': state.timestamp_ms,
        'servos':       servos,
    }


# ── POST /estop ───────────────────────────────────────────────────────────────

@app.post('/estop')
async def manual_estop():
    estop.trigger('HTTP /estop')
    return {'status': 'e-stop sent'}

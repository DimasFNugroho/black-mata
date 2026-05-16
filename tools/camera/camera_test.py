#!/usr/bin/env python3
"""
camera_test.py — Standalone MJPEG stream server for camera verification.

Tests the camera in isolation — no serial port or robot stack required.

Usage:
    python3 tools/camera/camera_test.py
    python3 tools/camera/camera_test.py --device 1
    python3 tools/camera/camera_test.py --device 1 --port 8083 --width 1280 --height 720

Then open in any browser:
    http://<host-ip>:<port>/stream
"""

import argparse
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from software.robot.camera import Camera

_camera: Camera = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request access log

    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                while True:
                    jpg = _camera.get_frame()
                    if jpg:
                        self.wfile.write(
                            b'--frame\r\n'
                            b'Content-Type: image/jpeg\r\n\r\n'
                            + jpg +
                            b'\r\n'
                        )
                    time.sleep(1.0 / _camera._fps)
            except (BrokenPipeError, ConnectionResetError):
                pass  # client disconnected

        elif self.path == '/':
            body = (
                b'<!DOCTYPE html><html><head><title>Camera Test</title>'
                b'<style>body{margin:0;background:#000;display:flex;'
                b'justify-content:center;align-items:center;height:100vh}'
                b'img{max-width:100%;max-height:100vh}</style></head>'
                b'<body><img src="/stream"></body></html>'
            )
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description='Standalone camera MJPEG test server')
    parser.add_argument('--device',  '-d', type=int,   default=0,    help='V4L2 device index (default: 0)')
    parser.add_argument('--port',    '-p', type=int,   default=8083, help='HTTP port (default: 8083)')
    parser.add_argument('--width',   '-W', type=int,   default=640,  help='Capture width (default: 640)')
    parser.add_argument('--height',  '-H', type=int,   default=480,  help='Capture height (default: 480)')
    parser.add_argument('--fps',     '-f', type=int,   default=30,   help='Capture FPS (default: 30)')
    parser.add_argument('--quality', '-q', type=int,   default=70,   help='JPEG quality 1-100 (default: 70)')
    args = parser.parse_args()

    global _camera
    _camera = Camera(args.device, args.width, args.height, args.fps, args.quality)
    try:
        _camera.start()
    except Exception as e:
        print(f'ERROR: Could not open camera device {args.device}: {e}')
        print('Try --device 1 or check: ls /dev/video*')
        sys.exit(1)

    server = HTTPServer(('0.0.0.0', args.port), Handler)
    print(f'Camera device {args.device} — {args.width}×{args.height} @ {args.fps} fps')
    print(f'Stream : http://0.0.0.0:{args.port}/stream')
    print(f'Browser: http://0.0.0.0:{args.port}/')
    print('Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping.')
    finally:
        _camera.stop()
        server.server_close()


if __name__ == '__main__':
    main()

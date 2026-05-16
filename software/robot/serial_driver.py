"""
serial_driver.py — Jetson-side binary serial driver for dxl_commander.

Sends CMD frames to the OpenCM9.04 and receives STATE frames in return.
A background thread (recv_loop) continuously reads incoming STATE frames
and caches the latest servo state, so callers never block on serial I/O.

Frame layout (must match dxl_commander.ino exactly):

  CMD frame  (host → OpenCM): 105 bytes
    [0]       START = 0xAA
    [1]       TYPE  = 0x01
    [2]       SEQ   uint8
    [3..6]    TIMESTAMP_MS uint32 LE
    [7..38]   servo_cmd[8] × 4 bytes:
                [+0] mode          0=JOINT 1=WHEEL
                [+1] enable_torque 0=off   1=on
                [+2] target_lo     uint16 LE (ticks for JOINT, speed for WHEEL)
                [+3] target_hi
    [39..102] payload[64] reserved (zeros)
    [103..104] CRC-16 CCITT (poly=0x1021, init=0xFFFF) over bytes [0..102]

  STATE frame (OpenCM → host): 202 bytes
    [0]       START = 0xAA
    [1]       TYPE  = 0x02
    [2]       SEQ   echoes CMD seq
    [3..6]    TIMESTAMP_MS uint32 LE (millis())
    [7]       e_stop  0=normal 1=watchdog fired
    [8..71]   imu[64] reserved (zeros)
    [72..135] servo_state[8] × 8 bytes:
                [+0] available  0 or 1
                [+1] mode       0=JOINT 1=WHEEL
                [+2] pos_lo     uint16 LE (0–1023 ticks)
                [+3] pos_hi
                [+4] speed_lo   uint16 LE
                [+5] speed_hi
                [+6] temperature uint8 (°C)
                [+7] voltage     uint8 (V = value × 0.1)
    [136..199] payload[64] reserved (zeros)
    [200..201] CRC-16 CCITT over bytes [0..199]

Usage:
    driver = SerialDriver('/dev/ttyACM0')
    driver.connect()
    driver.start()             # starts recv_loop background thread

    targets = [ServoCmd(mode=0, enable_torque=1, target=512)] * 4  # 4 steering
    targets += [ServoCmd(mode=1, enable_torque=1, target=0)]  * 4  # 4 wheels
    driver.send_frame(targets)

    state = driver.get_state()  # instant, no serial wait
    print(state)

    driver.stop()
"""

import struct
import threading
import time
import serial

# ── Frame constants ────────────────────────────────────────────────────────────
FRAME_START         = 0xAA
FRAME_TYPE_CMD      = 0x01
FRAME_TYPE_STATE    = 0x02

NUM_SERVOS          = 8
SERVO_IDS           = list(range(1, NUM_SERVOS + 1))  # IDs 1–8

CMD_FRAME_SIZE      = 105
STATE_FRAME_SIZE    = 202

CMD_OFF_SEQ         = 2
CMD_OFF_TS          = 3
CMD_OFF_SERVOS      = 7
CMD_SERVO_STRIDE    = 4
CMD_PAYLOAD_SIZE    = 64

STATE_OFF_SEQ       = 2
STATE_OFF_TS        = 3
STATE_OFF_ESTOP     = 7
STATE_OFF_IMU       = 8
STATE_OFF_SERVOS    = 72
STATE_SERVO_STRIDE  = 8
STATE_PAYLOAD_SIZE  = 64

DEFAULT_BAUD        = 115200
RECV_TIMEOUT        = 0.1   # seconds; serial read timeout in recv_loop
RECONNECT_DELAY     = 1.0

# ── CRC-16 CCITT ──────────────────────────────────────────────────────────────

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF  # truncate each bit iteration to match C++ uint16_t behaviour
    return crc

# ── Data types ─────────────────────────────────────────────────────────────────

class ServoCmd:
    """One servo's entry in a CMD frame."""
    __slots__ = ('mode', 'enable_torque', 'target')

    def __init__(self, mode: int = 0, enable_torque: int = 1, target: int = 0):
        """
        mode          : 0 = JOINT (position control), 1 = WHEEL (speed control)
        enable_torque : 0 = torque off, 1 = torque on
        target        : 0–1023 ticks (JOINT) or 0–2047 speed (WHEEL)
        """
        self.mode          = int(mode)
        self.enable_torque = int(enable_torque)
        self.target        = int(target)

    @staticmethod
    def neutral_joint(pos: int = 512) -> 'ServoCmd':
        return ServoCmd(mode=0, enable_torque=1, target=pos)

    @staticmethod
    def stop_wheel() -> 'ServoCmd':
        return ServoCmd(mode=1, enable_torque=1, target=0)

    @staticmethod
    def torque_off() -> 'ServoCmd':
        return ServoCmd(mode=0, enable_torque=0, target=0)


class ServoState:
    """One servo's state from a STATE frame."""
    __slots__ = ('servo_id', 'available', 'mode', 'pos', 'speed', 'temperature', 'voltage')

    def __init__(self, servo_id: int, available: bool, mode: int,
                 pos: int, speed: int, temperature: int, voltage_raw: int):
        self.servo_id    = servo_id
        self.available   = available
        self.mode        = mode
        self.pos         = pos            # 0–1023 ticks
        self.speed       = speed          # 0–2047
        self.temperature = temperature    # °C
        self.voltage     = voltage_raw * 0.1  # V

    def __repr__(self):
        mode_str = 'WHEEL' if self.mode else 'JOINT'
        if not self.available:
            return f'ServoState(id={self.servo_id}, UNAVAILABLE)'
        return (f'ServoState(id={self.servo_id}, {mode_str}, '
                f'pos={self.pos}, speed={self.speed}, '
                f'temp={self.temperature}°C, volt={self.voltage:.1f}V)')


class StateFrame:
    """Parsed STATE frame."""
    __slots__ = ('seq', 'timestamp_ms', 'e_stop', 'servos')

    def __init__(self, seq: int, timestamp_ms: int, e_stop: bool,
                 servos: list):
        self.seq          = seq
        self.timestamp_ms = timestamp_ms
        self.e_stop       = e_stop
        self.servos       = servos  # list of ServoState, index 0 = ID 1

# ── Frame builders / parsers ───────────────────────────────────────────────────

def build_cmd_frame(targets: list, seq: int, servo_ids: list = None,
                    timestamp_ms: int = None) -> bytes:
    """
    Build a 105-byte CMD frame.
    targets   : list of 8 ServoCmd objects in wheel-role order
                [FL_steer, FR_steer, RL_steer, RR_steer, FL_drive, FR_drive, RL_drive, RR_drive]
    seq       : rolling uint8 (0–255)
    servo_ids : physical DXL IDs for each role (same order as targets).
                Default [1,2,3,4,5,6,7,8] — slot i gets targets[i].
                If your wiring differs (e.g. RL_steer is ID 5), set accordingly.
    Returns the complete frame including CRC.
    """
    if len(targets) != NUM_SERVOS:
        raise ValueError('Expected {} servo targets, got {}'.format(NUM_SERVOS, len(targets)))

    if servo_ids is None:
        servo_ids = list(range(1, NUM_SERVOS + 1))

    if timestamp_ms is None:
        timestamp_ms = int(time.monotonic() * 1000) & 0xFFFFFFFF

    # Slots 0-7 correspond to physical servo IDs 1-8.
    # Place each target in the slot matching its physical ID.
    slots = [ServoCmd(mode=0, enable_torque=0, target=0)] * NUM_SERVOS
    for i, cmd in enumerate(targets):
        if i < len(servo_ids):
            sid = servo_ids[i]
            if 1 <= sid <= NUM_SERVOS:
                slots[sid - 1] = cmd

    buf = bytearray(CMD_FRAME_SIZE)
    buf[0] = FRAME_START
    buf[1] = FRAME_TYPE_CMD
    buf[2] = seq & 0xFF
    struct.pack_into('<I', buf, CMD_OFF_TS, timestamp_ms & 0xFFFFFFFF)

    for i, cmd in enumerate(slots):
        off = CMD_OFF_SERVOS + i * CMD_SERVO_STRIDE
        buf[off]     = cmd.mode & 0x01
        buf[off + 1] = cmd.enable_torque & 0x01
        struct.pack_into('<H', buf, off + 2, cmd.target & 0xFFFF)
    # payload [39..102] already zeros

    crc = crc16(bytes(buf[:CMD_FRAME_SIZE - 2]))
    struct.pack_into('>H', buf, CMD_FRAME_SIZE - 2, crc)
    return bytes(buf)


def parse_state_frame(raw: bytes):
    """
    Parse and validate a 202-byte STATE frame.
    Returns a StateFrame on success, or None if invalid.
    """
    if len(raw) != STATE_FRAME_SIZE:
        return None
    if raw[0] != FRAME_START or raw[1] != FRAME_TYPE_STATE:
        return None
    expected = crc16(raw[:STATE_FRAME_SIZE - 2])
    received = struct.unpack_from('>H', raw, STATE_FRAME_SIZE - 2)[0]
    if expected != received:
        return None

    seq          = raw[STATE_OFF_SEQ]
    timestamp_ms = struct.unpack_from('<I', raw, STATE_OFF_TS)[0]
    e_stop       = bool(raw[STATE_OFF_ESTOP])

    servos = []
    for i in range(NUM_SERVOS):
        off  = STATE_OFF_SERVOS + i * STATE_SERVO_STRIDE
        avail = bool(raw[off])
        mode  = raw[off + 1]
        pos   = struct.unpack_from('<H', raw, off + 2)[0]
        speed = struct.unpack_from('<H', raw, off + 4)[0]
        temp  = raw[off + 6]
        volt  = raw[off + 7]
        servos.append(ServoState(SERVO_IDS[i], avail, mode, pos, speed, temp, volt))

    return StateFrame(seq, timestamp_ms, e_stop, servos)

# ── SerialDriver ───────────────────────────────────────────────────────────────

class SerialDriver:
    """
    Thread-safe binary serial driver for dxl_commander.

    Call connect() then start() before sending frames.
    Call stop() to shut down the background recv thread cleanly.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD):
        self._port     = port
        self._baud     = baud
        self._ser      = None
        self._ser_lock = threading.Lock()

        self._seq      = 0
        self._seq_lock = threading.Lock()

        self._cache      = None          # latest StateFrame
        self._cache_lock = threading.Lock()

        self._running  = False
        self._thread   = None

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the serial port. DTR assertion resets the OpenCM for a clean start."""
        while True:
            try:
                ser = serial.Serial()
                ser.port     = self._port
                ser.baudrate = self._baud
                ser.timeout  = RECV_TIMEOUT
                ser.open()
                # Brief settle for USB enumeration, then drain stale bytes.
                time.sleep(0.2)
                ser.timeout = 0.05
                while ser.read(256):
                    pass
                ser.timeout = RECV_TIMEOUT
                with self._ser_lock:
                    self._ser = ser
                print('[SerialDriver] Connected: {} @ {}'.format(self._port, self._baud))
                return
            except serial.SerialException as e:
                print('[SerialDriver] {} — retrying in {}s'.format(e, RECONNECT_DELAY))
                time.sleep(RECONNECT_DELAY)

    def close(self) -> None:
        self.stop()
        with self._ser_lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
            self._ser = None

    # ── Background recv thread ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background STATE frame reader thread."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True, name='serial-recv')
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _recv_loop(self) -> None:
        """
        Continuously reads incoming bytes, synchronises on FRAME_START + TYPE_STATE,
        reads STATE_FRAME_SIZE bytes, validates CRC, and updates the cache.
        Reconnects automatically on serial errors.
        """
        buf = bytearray()

        while self._running:
            try:
                with self._ser_lock:
                    ser = self._ser
                if ser is None or not ser.is_open:
                    time.sleep(RECONNECT_DELAY)
                    continue

                chunk = ser.read(STATE_FRAME_SIZE)
                if not chunk:
                    continue
                buf.extend(chunk)

                # Scan for a valid frame start
                while len(buf) >= STATE_FRAME_SIZE:
                    idx = buf.find(bytes([FRAME_START, FRAME_TYPE_STATE]))
                    if idx == -1:
                        buf = buf[-1:]   # keep last byte (might be partial START)
                        break
                    if idx > 0:
                        buf = buf[idx:]  # discard leading garbage
                    if len(buf) < STATE_FRAME_SIZE:
                        break

                    frame_raw = bytes(buf[:STATE_FRAME_SIZE])
                    parsed    = parse_state_frame(frame_raw)
                    if parsed is not None:
                        with self._cache_lock:
                            self._cache = parsed
                        buf = buf[STATE_FRAME_SIZE:]
                    else:
                        buf = buf[2:]    # skip this START byte and retry

            except serial.SerialException as e:
                print(f'[SerialDriver] recv_loop error: {e}')
                time.sleep(RECONNECT_DELAY)
                buf.clear()

    # ── Transmit ───────────────────────────────────────────────────────────────

    def send_frame(self, targets: list, servo_ids: list = None) -> None:
        """
        Build and transmit a CMD frame.
        targets   : list of 8 ServoCmd objects in wheel-role order.
        servo_ids : physical DXL IDs for each role (see build_cmd_frame).
        Thread-safe; fire-and-forget.
        """
        with self._seq_lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFF

        frame = build_cmd_frame(targets, seq, servo_ids)

        with self._ser_lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(frame)
                except serial.SerialException as e:
                    print(f'[SerialDriver] send_frame error: {e}')

    def send_estop(self) -> None:
        """Disable torque on all servos — robot goes limp (safe, no forced movement)."""
        targets = [ServoCmd.torque_off()] * NUM_SERVOS
        self.send_frame(targets)

    # ── State access ───────────────────────────────────────────────────────────

    def get_state(self):
        """Return the latest cached StateFrame, or None if none received yet."""
        with self._cache_lock:
            return self._cache

    def get_servo(self, servo_id: int):
        """Return ServoState for the given ID (1–8), or None."""
        state = self.get_state()
        if state is None:
            return None
        idx = servo_id - 1
        if 0 <= idx < NUM_SERVOS:
            return state.servos[idx]
        return None

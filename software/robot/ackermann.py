"""
ackermann.py — 4-Wheel Steering + 4-Wheel Drive Ackermann kinematics.

Takes a steering angle δ (degrees) and speed v (m/s) and produces 8
ServoCmd objects — 4 steering (JOINT mode) and 4 drive (WHEEL mode) —
ready to pass directly to SerialDriver.send_frame().

Coordinate convention
---------------------
  +x  = forward
  +y  = left
  +δ  = right turn  (positive steer_deg turns the robot right)
  +v  = forward     (positive speed_mps moves the robot forward)

4WS counter-phase geometry
--------------------------
Front and rear axles steer in opposite directions. The instantaneous
rotation centre lies on the vehicle's lateral midline (equidistant
from front and rear axles). This gives tighter turning and better
stability than front-only steering.

                        R (lateral)
         ───────────────────────────── rotation centre
         │                           │
    FL ──┤ L/2 forward               │ FR
         │                           │
        (0,0) vehicle centre         │
         │                           │
    RL ──┤ L/2 rearward              │ RR
         │                           │
         ────────────── W ────────────

Per-wheel Ackermann angles for a right turn (δ > 0):
  FL (outer front):  +arctan( (L/2) / (R + W/2) )
  FR (inner front):  +arctan( (L/2) / (R - W/2) )
  RL (outer rear):   −arctan( (L/2) / (R + W/2) )   counter-phase
  RR (inner rear):   −arctan( (L/2) / (R - W/2) )   counter-phase

Turning radius R = (L/2) / tan(|δ|), where δ is the input command angle.

Per-wheel speed scaling:
  Each wheel's speed is proportional to its distance from the rotation centre.
  The fastest wheel (outer) is scaled to max speed; others scale accordingly.

Servo IDs (must match physical wiring and dxl_commander):
  1 = FL steer  2 = FR steer  3 = RL steer  4 = RR steer  (JOINT)
  5 = FL drive  6 = FR drive  7 = RL drive  8 = RR drive  (WHEEL)

AX-12A WHEEL mode speed encoding:
  0        = stop
  1–1023   = rotate in one direction   (call it CCW, raw = speed)
  1024     = stop
  1025–2047 = rotate in other direction (CW,  raw = 1024 + speed)

  drive_dir[i] = +1 → forward maps to CCW (raw = speed ticks)
  drive_dir[i] = −1 → forward maps to CW  (raw = 1024 + speed ticks)
  Left and right wheels are mirror-mounted so they need opposite signs.

Tuning notes
------------
  steer_dir  : verify by commanding a small positive steer_deg and
               checking which way the robot turns. Flip signs if reversed.
  drive_dir  : verify by commanding small positive speed_mps and
               checking that all wheels spin forward. Flip signs if reversed.
  steer_center_ticks : should produce the physical neutral (straight) position.
               Default 512. Measure and adjust per servo if needed.
"""

import math
from dataclasses import dataclass, field
from typing import List

from software.robot.serial_driver import ServoCmd

# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class AckermannConfig:
    # Physical dimensions (metres)
    wheelbase:   float = 0.20   # L: front-to-rear axle centre distance
    track_width: float = 0.15   # W: left-to-right wheel centre distance

    # Motion limits
    max_steer_deg:        float = 30.0   # clamp on input δ
    max_wheel_speed_ticks: int  = 300    # DXL WHEEL output cap (0–1023); 1023 = 100% output

    # Steer servo parameters (AX-12A: 300° range, 1023 ticks)
    steer_center_ticks: int   = 512              # tick at physical neutral
    ticks_per_deg:      float = 1023.0 / 300.0  # ≈ 3.41 ticks/degree

    # Mounting direction signs — flip to match physical installation
    # Index order: [FL, FR, RL, RR]
    # steer_dir[i] = +1  →  positive angle  increases tick count
    # steer_dir[i] = −1  →  positive angle  decreases tick count
    steer_dir: List[int] = field(default_factory=lambda: [1, -1, -1, 1])

    # drive_dir[i] = +1  →  positive v  sends CCW (raw speed ticks, no offset)
    # drive_dir[i] = −1  →  positive v  sends CW  (raw = 1024 + speed ticks)
    drive_dir: List[int] = field(default_factory=lambda: [1, -1, 1, -1])

    # Per-wheel steering angle offset (degrees) added after Ackermann computation.
    # Use to correct physical misalignment: if a wheel is physically 2° off neutral,
    # set its offset to −2.0 to cancel the error.
    steer_offset_deg: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # Physical Dynamixel IDs for each wheel role.
    # Order: [FL_steer, FR_steer, RL_steer, RR_steer, FL_drive, FR_drive, RL_drive, RR_drive]
    # Change these to match your actual wiring if servos are not in 1-8 order.
    # Physical DXL IDs for each wheel role (discovered via dxl_identify.py):
    # FL_steer=4, FR_steer=2, RL_steer=8, RR_steer=6, FL_drive=3, FR_drive=1, RL_drive=7, RR_drive=5
    servo_ids: List[int] = field(default_factory=lambda: [4, 2, 8, 6, 3, 1, 7, 5])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _encode_wheel_speed(fraction: float, direction: int, max_ticks: int) -> int:
    """
    Encode a signed output fraction (−1.0 … +1.0) into AX-12A WHEEL mode value.
    fraction  : −1.0 … +1.0  (output level; sign sets forward/backward)
    direction : +1 or −1     (servo mounting direction)
    max_ticks : output cap, 0–1023 (hardware limit; values above 1023 are clamped)
    Returns   : 0–2047 as required by WHEEL mode.
    """
    max_ticks = min(max_ticks, 1023)
    effective = fraction * direction
    raw = min(int(abs(effective) * max_ticks), 1023)
    if effective >= 0:
        return raw          # CCW direction
    else:
        return 1024 + raw   # CW direction


def _angle_to_ticks(angle_deg: float, direction: int, center: int,
                    ticks_per_deg: float) -> int:
    """Convert a physical steering angle (degrees) to servo ticks."""
    ticks = center + direction * angle_deg * ticks_per_deg
    return int(max(0, min(1023, round(ticks))))


# ── Kinematics ────────────────────────────────────────────────────────────────

class Ackermann:
    """
    4WS + 4WD Ackermann kinematics calculator.

    Usage:
        cfg = AckermannConfig(wheelbase=0.20, track_width=0.15)
        ack = Ackermann(cfg)
        targets = ack.compute(steer_deg=15.0, speed_mps=0.3)
        driver.send_frame(targets)
    """

    def __init__(self, config: AckermannConfig = None):
        self.cfg = config or AckermannConfig()

    def compute(self, steer_deg: float, speed_frac: float) -> List[ServoCmd]:
        """
        Compute 8 servo targets from steering angle and drive output fraction.

        steer_deg  : desired steering angle in degrees (+right, −left)
        speed_frac : drive output fraction −1.0 … +1.0
                     (+1.0 = max_wheel_speed_ticks output forward,
                      −1.0 = max_wheel_speed_ticks output reverse)
        Returns    : list of 8 ServoCmd — indices 0–3 are steer, indices 4–7 are drive.
        """
        cfg = self.cfg

        # ── Clamp inputs ───────────────────────────────────────────────────────
        δ_deg  = max(-cfg.max_steer_deg, min(cfg.max_steer_deg, steer_deg))
        v_frac = max(-1.0, min(1.0, speed_frac))

        L2 = cfg.wheelbase   / 2.0
        W2 = cfg.track_width / 2.0

        # ── Steer angles ───────────────────────────────────────────────────────
        if abs(δ_deg) < 0.5:
            # Straight — all steer servos at neutral, equal wheel speeds
            fl_deg = fr_deg = rl_deg = rr_deg = 0.0
            speed_scale = [1.0, 1.0, 1.0, 1.0]

        else:
            δ_rad = math.radians(abs(δ_deg))
            sign  = 1 if δ_deg > 0 else -1

            # Lateral turning radius (vehicle centre to rotation centre)
            R = L2 / math.tan(δ_rad)

            # Outer wheel is further from the rotation centre, inner is closer.
            # For a right turn (sign=+1): FL/RL are outer, FR/RR are inner.
            # For a left turn  (sign=-1): FR/RR are outer, FL/RL are inner.
            outer_abs = math.degrees(math.atan2(L2, R + W2))
            inner_abs = math.degrees(math.atan2(L2, R - W2))

            if sign > 0:  # right turn
                fl_deg, fr_deg =  outer_abs,  inner_abs
            else:          # left turn
                fl_deg, fr_deg = -inner_abs, -outer_abs

            rl_deg = -fl_deg  # counter-phase
            rr_deg = -fr_deg  # counter-phase

            # ── Speed scaling ─────────────────────────────────────────────────
            r_outer = math.sqrt(L2 ** 2 + (R + W2) ** 2)
            r_inner = math.sqrt(L2 ** 2 + (R - W2) ** 2)

            # For very tight turns (R < W2) the inner wheel passes the rotation
            # centre and must spin backward relative to the outer wheel.
            inner_sign = 1.0 if R >= W2 else -1.0

            if sign > 0:  # right turn: FL/RL outer, FR/RR inner
                speed_scale = [
                    1.0,
                    inner_sign * r_inner / r_outer,
                    1.0,
                    inner_sign * r_inner / r_outer,
                ]
            else:          # left turn: FR/RR outer, FL/RL inner
                speed_scale = [
                    inner_sign * r_inner / r_outer,
                    1.0,
                    inner_sign * r_inner / r_outer,
                    1.0,
                ]

        # ── Build steer ServoCmd objects (IDs 1–4, JOINT mode) ────────────────
        angles = [fl_deg, fr_deg, rl_deg, rr_deg]
        steer_cmds = [
            ServoCmd(
                mode=0,
                enable_torque=1,
                target=_angle_to_ticks(
                    angles[i] + cfg.steer_offset_deg[i], cfg.steer_dir[i],
                    cfg.steer_center_ticks, cfg.ticks_per_deg
                ),
            )
            for i in range(4)
        ]

        # ── Build drive ServoCmd objects (IDs 5–8, WHEEL mode) ────────────────
        drive_cmds = [
            ServoCmd(
                mode=1,
                enable_torque=1,
                target=_encode_wheel_speed(
                    v_frac * speed_scale[i],
                    cfg.drive_dir[i],
                    cfg.max_wheel_speed_ticks,
                ),
            )
            for i in range(4)
        ]

        return steer_cmds + drive_cmds

    def estop_targets(self) -> List[ServoCmd]:
        """Return 8 targets that zero drive speed and centre steering (offsets applied)."""
        return self.compute(0.0, 0.0)

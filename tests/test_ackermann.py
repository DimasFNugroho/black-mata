"""
Tests for software/robot/ackermann.py.

Run from repo root:
    python -m pytest tests/test_ackermann.py -v
"""

import math
import pytest

from software.robot.ackermann import Ackermann, AckermannConfig, _encode_wheel_speed, _angle_to_ticks


CFG = AckermannConfig()
ACK = Ackermann(CFG)


# ── _encode_wheel_speed ────────────────────────────────────────────────────────

class TestEncodeWheelSpeed:
    def test_stopped(self):
        assert _encode_wheel_speed(0.0, 1, 300) == 0

    def test_full_forward_ccw(self):
        # direction=+1, fraction=+1.0 → CCW → raw ticks (no offset)
        assert _encode_wheel_speed(1.0, 1, 300) == 300

    def test_full_forward_cw(self):
        # direction=−1, fraction=+1.0 → effective=−1.0 → CW → 1024+300
        assert _encode_wheel_speed(1.0, -1, 300) == 1024 + 300

    def test_half_speed(self):
        assert _encode_wheel_speed(0.5, 1, 300) == 150

    def test_backward(self):
        # direction=+1, fraction=−1.0 → effective=−1.0 → CW
        assert _encode_wheel_speed(-1.0, 1, 300) == 1024 + 300

    def test_clamp_at_1023(self):
        # max_ticks above 1023 should clamp
        result = _encode_wheel_speed(1.0, 1, 2000)
        assert result == 1023


# ── _angle_to_ticks ────────────────────────────────────────────────────────────

class TestAngleToTicks:
    def test_neutral(self):
        assert _angle_to_ticks(0.0, 1, 512, CFG.ticks_per_deg) == 512

    def test_positive_angle_positive_dir(self):
        ticks = _angle_to_ticks(10.0, 1, 512, CFG.ticks_per_deg)
        assert ticks > 512

    def test_positive_angle_negative_dir(self):
        ticks = _angle_to_ticks(10.0, -1, 512, CFG.ticks_per_deg)
        assert ticks < 512

    def test_clamp_low(self):
        assert _angle_to_ticks(-200.0, 1, 512, CFG.ticks_per_deg) == 0

    def test_clamp_high(self):
        assert _angle_to_ticks(200.0, 1, 512, CFG.ticks_per_deg) == 1023


# ── Ackermann.compute — straight ───────────────────────────────────────────────

class TestStraight:
    def test_returns_8_cmds(self):
        cmds = ACK.compute(0.0, 0.0)
        assert len(cmds) == 8

    def test_steer_neutral_at_zero(self):
        cmds = ACK.compute(0.0, 0.3)
        for i in range(4):
            assert cmds[i].target == CFG.steer_center_ticks

    def test_all_drive_equal_speed(self):
        cmds = ACK.compute(0.0, 0.3)
        # Left and right wheels are mirror-mounted: same magnitude but different CW/CCW encoding.
        # Decode magnitude: CW = target - 1024, CCW = target (no offset).
        def mag(target):
            return target - 1024 if target >= 1024 else target
        speeds = [mag(cmds[i].target) for i in range(4, 8)]
        assert len(set(speeds)) == 1, f'Expected equal speed magnitudes, got {speeds}'

    def test_zero_speed_stopped(self):
        cmds = ACK.compute(0.0, 0.0)
        for i in range(4, 8):
            assert cmds[i].target == 0

    def test_modes(self):
        cmds = ACK.compute(0.0, 0.0)
        for i in range(4):
            assert cmds[i].mode == 0   # JOINT
        for i in range(4, 8):
            assert cmds[i].mode == 1   # WHEEL


# ── Ackermann.compute — turning geometry ───────────────────────────────────────

class TestTurning:
    def _steer_angles(self, steer_deg):
        """Return (fl, fr, rl, rr) tick values for a given steer input."""
        cmds = ACK.compute(steer_deg, 0.1)
        return [cmds[i].target for i in range(4)]

    def test_right_turn_front_outer_less_than_inner(self):
        # Right turn (δ>0): FL is outer, FR is inner.
        # Inner front wheel turns more sharply → larger angle away from centre.
        fl, fr, rl, rr = self._steer_angles(15.0)
        # steer_dir = [+1, −1, −1, +1]
        # FL outer: centre + angle → tick > 512
        # FR inner: centre − angle → tick < 512 (larger magnitude)
        assert fl > CFG.steer_center_ticks, 'FL should be above neutral'
        assert fr < CFG.steer_center_ticks, 'FR should be below neutral'

    def test_counter_phase_rear(self):
        # Decode raw ticks back to physical steering angles using steer_dir.
        # steer_dir = [FL=+1, FR=-1, RL=-1, RR=+1]
        # physical_angle = (tick - center) / (steer_dir * ticks_per_deg)
        fl, fr, rl, rr = self._steer_angles(15.0)
        tpd = CFG.ticks_per_deg
        c   = CFG.steer_center_ticks
        fl_phys = (fl - c) / ( 1 * tpd)
        rl_phys = (rl - c) / (-1 * tpd)
        # Counter-phase: front and rear must steer in opposite physical directions
        assert fl_phys > 0, 'FL should steer right for positive input'
        assert rl_phys < 0, 'RL should steer left (counter-phase) for positive input'

    def test_left_turn_mirrors_right(self):
        right = self._steer_angles(15.0)
        left  = self._steer_angles(-15.0)
        # Ackermann symmetry: FL↔FR and RL↔RR swap between left and right turns.
        # The inner and outer angles differ, so ticks do NOT mirror around 512 —
        # instead each wheel's right-turn tick equals the opposing wheel's left-turn tick.
        assert right[0] == left[1], f'FL right {right[0]} should equal FR left {left[1]}'
        assert right[1] == left[0], f'FR right {right[1]} should equal FL left {left[0]}'
        assert right[2] == left[3], f'RL right {right[2]} should equal RR left {left[3]}'
        assert right[3] == left[2], f'RR right {right[3]} should equal RL left {left[2]}'

    def test_inner_front_turns_more_than_outer(self):
        # |FR angle| > |FL angle| for a right turn (inner wheel sweeps tighter arc)
        cmds = ACK.compute(15.0, 0.1)
        fl_deg = (cmds[0].target - 512) / CFG.ticks_per_deg
        fr_deg = (cmds[1].target - 512) / CFG.ticks_per_deg
        assert abs(fr_deg) > abs(fl_deg)

    def test_outer_wheel_faster_than_inner(self):
        # FL (outer) should have higher speed than FR (inner) during a right turn
        cmds = ACK.compute(15.0, 0.3)
        fl_raw = cmds[4].target  # FL drive
        fr_raw = cmds[5].target  # FR drive
        # Both CCW for forward motion with default drive_dir=[+1,−1,+1,−1]
        # FL: CCW → raw ticks (no offset); FR: CW → 1024+ticks
        fl_ticks = fl_raw            # CCW, direction=+1
        fr_ticks = fr_raw - 1024     # CW, direction=−1
        assert fl_ticks > fr_ticks, \
            f'Outer FL ({fl_ticks}) should be faster than inner FR ({fr_ticks})'

    def test_clamp_max_steer(self):
        # Clamped input should give same result as max_steer_deg
        cmds_clamped = ACK.compute(999.0, 0.1)
        cmds_max     = ACK.compute(CFG.max_steer_deg, 0.1)
        for a, b in zip(cmds_clamped, cmds_max):
            assert a.target == b.target


# ── Ackermann.estop_targets ────────────────────────────────────────────────────

class TestEstop:
    def test_steer_neutral(self):
        cmds = ACK.estop_targets()
        for i in range(4):
            assert cmds[i].target == CFG.steer_center_ticks

    def test_drive_zero(self):
        cmds = ACK.estop_targets()
        for i in range(4, 8):
            assert cmds[i].target == 0

    def test_torque_on(self):
        cmds = ACK.estop_targets()
        for cmd in cmds:
            assert cmd.enable_torque == 1

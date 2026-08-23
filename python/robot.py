import time
import math
from pca9685 import PCA9685

class SpiderBot:
    def __init__(self):
        self.pca = PCA9685()

        self.centers = {
            "RL": {"hip": 1600, "joint": 1500, "foot": 1500},
            "RR": {"hip": 1550, "joint": 1500, "foot": 1500},
            "FL": {"hip": 1500, "joint": 1700, "foot": 1500},
            "FR": {"hip": 1400, "joint": 1300, "foot": 1500}
        }

        self.channels = {
            "RL": {"hip": 3, "joint": 2, "foot": 1},
            "RR": {"hip": 12, "joint": 6, "foot": 5},
            "FL": {"hip": 11, "joint": 10, "foot": 9},
            "FR": {"hip": 14, "joint": 15, "foot": 13}
        }

        self.directions = {
            "RL": {"hip": 1,  "joint": -1, "foot": 1},
            "RR": {"hip": -1, "joint": 1,  "foot": -1},
            "FL": {"hip": 1,  "joint": 1,  "foot": -1},
            "FR": {"hip": -1, "joint": -1, "foot": 1}
        }

        self.stand_hip_offset = 0
        self.stand_joint_offset = -220  # Squat stance reduces lever torque on servos
        self.stand_foot_offset = 220

        self.body_height = 0.0
        self.step_height = 90.0         # Lower step lift keeps 4 legs closer to ground
        self.stride_amp = 450.0         # Shorter strides prevent joint strain
        self.cycle_time = 2.0

        self.yaw_trim = 0.82

        self.phase_offsets = {
            "RL": 0.0,
            "FL": 0.25,
            "RR": 0.5,
            "FR": 0.75
        }

        self.state = "STAND"
        self.vx = 0.0
        self.omega = 0.0
        self.start_time = time.time()

        # Stabilization
        self.stabilize_enabled = False
        self.stabilize_gain = 0.015
        self.stabilize_deadzone = 400
        self.stabilize_clamp = 150.0
        self._stabilize_alpha = 0.15
        self._filt_ax = 0
        self._filt_ay = 0
        self._imu_ax = 0
        self._imu_ay = 0
        self._imu_az = 0

    def set_imu(self, ax: int, ay: int, az: int):
        self._imu_ax = ax
        self._imu_ay = ay
        self._imu_az = az
        self._filt_ax = self._stabilize_alpha * ax + (1.0 - self._stabilize_alpha) * self._filt_ax
        self._filt_ay = self._stabilize_alpha * ay + (1.0 - self._stabilize_alpha) * self._filt_ay

    def _stabilize_offset(self) -> dict:
        ax = self._filt_ax
        ay = self._filt_ay
        if abs(ax) < self.stabilize_deadzone and abs(ay) < self.stabilize_deadzone:
            return {leg: 0.0 for leg in ["RL", "RR", "FL", "FR"]}

        g = self.stabilize_gain
        fx = g * ax if abs(ax) > self.stabilize_deadzone else 0.0
        fy = g * ay if abs(ay) > self.stabilize_deadzone else 0.0

        raw = {
            "FL":  fx + fy,
            "FR":  fx - fy,
            "RL": -fx + fy,
            "RR": -fx - fy
        }
        return {leg: max(-self.stabilize_clamp, min(self.stabilize_clamp, v)) for leg, v in raw.items()}

    def set_joint_offset(self, leg: str, joint_type: str, offset: float):
        ch = self.channels[leg][joint_type]
        center = self.centers[leg][joint_type]
        direction = self.directions[leg][joint_type]
        pulse = center + (offset * direction)
        self.pca.set_pulse_width(ch, pulse)

    def stand(self):
        self.state = "STAND"
        self.vx = 0.0
        self.omega = 0.0
        for leg in ["RL", "RR", "FL", "FR"]:
            self.set_joint_offset(leg, "hip", self.stand_hip_offset)
            self.set_joint_offset(leg, "joint", self.stand_joint_offset + self.body_height)
            self.set_joint_offset(leg, "foot", self.stand_foot_offset)
        self.pca.write_batch()

    def walkForward(self, speed: float = 1.0):
        self.stabilize_enabled = False
        self.state = "WALK"
        self.vx = speed
        self.omega = 0.0
        self.start_time = time.time()

    def walkBackward(self, speed: float = 1.0):
        self.stabilize_enabled = False
        self.state = "WALK"
        self.vx = -speed
        self.omega = 0.0
        self.start_time = time.time()

    def turnLeft(self, rate: float = 1.0):
        self.stabilize_enabled = False
        self.state = "WALK"
        self.vx = 0.0
        self.omega = -rate
        self.start_time = time.time()

    def turnRight(self, rate: float = 1.0):
        self.stabilize_enabled = False
        self.state = "WALK"
        self.vx = 0.0
        self.omega = rate
        self.start_time = time.time()

    def stabilize(self, enable: bool = None):
        if enable is not None:
            self.stabilize_enabled = enable
        else:
            self.stabilize_enabled = not self.stabilize_enabled

    def stop(self):
        self.stabilize_enabled = False
        self.stand()

    def _apply_stabilize(self):
        if not self.stabilize_enabled:
            return
        corr = self._stabilize_offset()
        for leg in ["RL", "RR", "FL", "FR"]:
            c = corr[leg]
            self.set_joint_offset(leg, "joint", self.stand_joint_offset + self.body_height + c)
            self.set_joint_offset(leg, "foot", self.stand_foot_offset + c)

    def update(self):
        if self.state == "STAND":
            for leg in ["RL", "RR", "FL", "FR"]:
                self.set_joint_offset(leg, "hip", self.stand_hip_offset)
                self.set_joint_offset(leg, "joint", self.stand_joint_offset + self.body_height)
                self.set_joint_offset(leg, "foot", self.stand_foot_offset)
            self._apply_stabilize()

        elif self.state == "WALK":
            elapsed = time.time() - self.start_time
            for leg in ["RL", "RR", "FL", "FR"]:
                offset = self.phase_offsets[leg]
                phase = (elapsed / self.cycle_time - offset) % 1.0
                leg_stride_amp = self.stride_amp
                if leg in ["RR", "FR"]:
                    leg_stride_amp *= self.yaw_trim
                j_stand = self.stand_joint_offset + self.body_height
                f_stand = self.stand_foot_offset
                if phase < 0.25:
                    p_swing = phase / 0.25
                    lift = self.step_height * math.sin(math.pi * p_swing)
                    j_off = j_stand - lift
                    f_off = f_stand - lift
                    if self.vx != 0.0:
                        h_off = self.stand_hip_offset + self.vx * leg_stride_amp * (p_swing - 0.5)
                    else:
                        direction_factor = 1.0 if leg in ["FL", "RL"] else -1.0
                        h_off = self.stand_hip_offset + self.omega * direction_factor * leg_stride_amp * (p_swing - 0.5)
                else:
                    p_stance = (phase - 0.25) / 0.75
                    support_ext = 80.0 * math.sin(math.pi * p_stance)
                    j_off = j_stand + (support_ext * 0.5)
                    f_off = f_stand + support_ext
                    if self.vx != 0.0:
                        h_off = self.stand_hip_offset + self.vx * leg_stride_amp * (0.5 - p_stance)
                    else:
                        direction_factor = 1.0 if leg in ["FL", "RL"] else -1.0
                        h_off = self.stand_hip_offset + self.omega * direction_factor * leg_stride_amp * (0.5 - p_stance)
                self.set_joint_offset(leg, "hip", h_off)
                self.set_joint_offset(leg, "joint", j_off)
                self.set_joint_offset(leg, "foot", f_off)

        self.pca.write_batch()

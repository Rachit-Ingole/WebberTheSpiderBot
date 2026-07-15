import math
from kinematics import LegKinematics

class Leg:
    def __init__(
        self,
        name: str,
        pca,                             # PCA9685 proxy (Bridge-backed)
        channels: tuple,                 # (coxa_ch, femur_ch, tibia_ch)
        link_lengths: tuple,             # (l1, l2, l3) in mm
        center_pw: tuple = (1500.0, 1500.0, 1500.0),  # center pulse widths in us
        directions: tuple = (1, 1, 1),   # 1 or -1 per joint
        offsets: tuple = (0.0, 0.0, 0.0) # angle offsets in radians
    ):
        self.name = name
        self.pca = pca
        self.channels = channels
        self.kinematics = LegKinematics(*link_lengths)
        self.center_pw = center_pw
        self.directions = directions
        self.offsets = offsets

        # Current target position and angles
        self.current_pos = (0.0, 0.0, 0.0)
        self.current_angles = (0.0, 0.0, 0.0)

        # Microseconds per radian conversion: 2000 us / pi rad
        self.us_per_rad = 2000.0 / math.pi

    def write_joint_angle(self, joint_idx: int, angle_rad: float):
        """Converts joint angle to pulse width and commands the servo."""
        ch = self.channels[joint_idx]
        center = self.center_pw[joint_idx]
        direction = self.directions[joint_idx]
        offset = self.offsets[joint_idx]

        # Apply offset and direction
        final_angle = (angle_rad + offset) * direction
        
        # Calculate pulse width
        pw = center + (final_angle * self.us_per_rad)
        
        # Safety limits (500us to 2500us)
        pw = max(500.0, min(2500.0, pw))
        
        self.pca.set_pulse_width(ch, pw)

    def move_to_angles(self, coxa: float, femur: float, tibia: float):
        """Commands the leg joints directly to specified angles in radians."""
        self.current_angles = (coxa, femur, tibia)
        self.write_joint_angle(0, coxa)
        self.write_joint_angle(1, femur)
        self.write_joint_angle(2, tibia)

    def move_to_position(self, x: float, y: float, z: float):
        """Solves IK and moves the leg to Cartesian target (x, y, z)."""
        self.current_pos = (x, y, z)
        try:
            angles = self.kinematics.solve_ik(x, y, z)
            self.move_to_angles(*angles)
        except ValueError as e:
            # Handle out of reach warnings/errors
            pass

import math

class CreepGaitGenerator:
    def __init__(self, stride_len: float = 40.0, step_height: float = 20.0, cycle_time: float = 2.0):
        self.stride_len = stride_len
        self.step_height = step_height
        self.cycle_time = cycle_time
        
        # Phase offsets for the 4 legs (creep gait) in order: RL, RR, FL, FR
        # Diagonal pairs: RL+FR, RR+FL
        self.phase_offsets = [0.75, 0.5, 0.25, 0.0]
        
        # Duty factor for swing phase is 0.25 (one leg swings at a time)
        self.swing_duty = 0.25

    def get_foot_position(
        self,
        leg_idx: int,
        t_global: float,
        neutral_pos: tuple[float, float, float],
        vx: float,
        vy: float,
        omega: float
    ) -> tuple[float, float, float]:
        """
        Calculates the target (x, y, z) coordinate for a leg at global time t_global,
        given movement vectors: vx (forward), vy (lateral), omega (yaw rotation).
        """
        x_n, y_n, z_n = neutral_pos

        # Local phase for this leg
        offset = self.phase_offsets[leg_idx]
        phase = (t_global / self.cycle_time - offset) % 1.0

        if phase < self.swing_duty:
            # --- SWING PHASE ---
            # Normalized swing progress from 0.0 to 1.0
            p_swing = phase / self.swing_duty
            
            # Interpolate from starting position to ending position
            # Swing moves in the direction of velocity (vx, vy)
            # From -stride/2 to +stride/2
            dx = vx * self.stride_len * (p_swing - 0.5)
            dy = vy * self.stride_len * (p_swing - 0.5)
            
            # Parabolic height curve
            dz = self.step_height * math.sin(math.pi * p_swing)
        else:
            # --- STANCE PHASE ---
            # Normalized stance progress from 0.0 to 1.0
            p_stance = (phase - self.swing_duty) / (1.0 - self.swing_duty)
            
            # Stance moves opposite to velocity to push the body forward
            # From +stride/2 to -stride/2
            dx = vx * self.stride_len * (0.5 - p_stance)
            dy = vy * self.stride_len * (0.5 - p_stance)
            dz = 0.0

        # Adjust for rotational velocity (omega) if needed (simplified yaw rotation)
        # For simplicity, we can apply rotation to vx/vy depending on leg position
        return x_n + dx, y_n + dy, z_n + dz

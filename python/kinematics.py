import math

class LegKinematics:
    def __init__(self, coxa_len: float, femur_len: float, tibia_len: float):
        self.l1 = coxa_len
        self.l2 = femur_len
        self.l3 = tibia_len

    def solve_ik(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """
        Solves Inverse Kinematics for a 3-DOF leg.
        Returns target angles in radians: (coxa, femur, tibia).
        """
        # Coxa angle
        theta1 = math.atan2(y, x)

        # Distance from coxa joint to foot projected on XY plane
        d_xy = math.sqrt(x*x + y*y)
        # Distance from femur joint to foot projected
        d = d_xy - self.l1

        # Distance from femur to foot target in 3D
        s = math.sqrt(d*d + z*z)

        # Clamping to avoid math domain errors for unreachable targets
        max_reach = self.l2 + self.l3
        min_reach = abs(self.l2 - self.l3)
        if s > max_reach:
            s = max_reach
        elif s < min_reach:
            s = min_reach

        # Law of Cosines for femur angle
        # alpha is angle between femur and target line s
        cos_alpha = (self.l2*self.l2 + s*s - self.l3*self.l3) / (2.0 * self.l2 * s)
        cos_alpha = max(-1.0, min(1.0, cos_alpha))
        alpha = math.acos(cos_alpha)

        # beta is angle of target line s to horizontal
        beta = math.atan2(z, d)

        # Femur angle relative to horizontal (pointing down/out)
        theta2 = beta + alpha

        # Law of Cosines for tibia angle
        # gamma is angle between femur and tibia
        cos_gamma = (self.l2*self.l2 + self.l3*self.l3 - s*s) / (2.0 * self.l2 * self.l3)
        cos_gamma = max(-1.0, min(1.0, cos_gamma))
        gamma = math.acos(cos_gamma)

        # Tibia angle relative to femur extension (usually negative/bent down)
        theta3 = gamma - math.pi

        return theta1, theta2, theta3

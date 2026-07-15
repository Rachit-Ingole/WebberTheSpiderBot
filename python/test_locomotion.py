import math
from kinematics import LegKinematics

def test_ik():
    # Initialize kinematics solver
    l1, l2, l3 = 30.0, 50.0, 80.0
    solver = LegKinematics(l1, l2, l3)
    
    # Test a target position
    x, y, z = 40.0, 50.0, -80.0
    print(f"Testing target: X={x}, Y={y}, Z={z}")
    
    theta1, theta2, theta3 = solver.solve_ik(x, y, z)
    
    # Print angles in degrees
    t1_deg = theta1 * 180.0 / math.pi
    t2_deg = theta2 * 180.0 / math.pi
    t3_deg = theta3 * 180.0 / math.pi
    
    print(f"Calculated angles (deg): Coxa={t1_deg:.2f}, Femur={t2_deg:.2f}, Tibia={t3_deg:.2f}")

    # Forward Kinematics verification to ensure they match
    # Projection on XY plane
    d_xy = l1 + l2 * math.cos(theta2) + l3 * math.cos(theta2 + theta3)
    fk_x = d_xy * math.cos(theta1)
    fk_y = d_xy * math.sin(theta1)
    fk_z = l2 * math.sin(theta2) + l3 * math.sin(theta2 + theta3)
    
    print(f"FK reconstructed: X={fk_x:.2f}, Y={fk_y:.2f}, Z={fk_z:.2f}")
    
    assert abs(fk_x - x) < 0.1, f"X mismatch: {fk_x} != {x}"
    assert abs(fk_y - y) < 0.1, f"Y mismatch: {fk_y} != {y}"
    assert abs(fk_z - z) < 0.1, f"Z mismatch: {fk_z} != {z}"
    print("Success! IK and FK match perfectly.")

if __name__ == "__main__":
    test_ik()

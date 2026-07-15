import time
from machine import I2C, Pin
from pca9685 import PCA9685

# Initialize I2C and PCA9685
try:
    i2c = I2C(0)
except Exception:
    try:
        i2c = I2C(1)
    except Exception:
        i2c = I2C(sda=Pin(4), scl=Pin(5))

pca = PCA9685(i2c)

# Servo channels mapped to parts
# Order: RL(Foot=1, Joint=2, Hip=3), RR(Foot=5, Joint=6, Hip=12), FL(Foot=9, Joint=10, Hip=11), FR(Foot=13, Joint=14, Hip=15)
channel_names = {
    1: "RL Foot",
    2: "RL Joint",
    3: "RL Hip",
    5: "RR Foot",
    6: "RR Joint",
    12: "RR Hip",
    9: "FL Foot",
    10: "FL Joint",
    11: "FL Hip",
    13: "FR Foot",
    14: "FR Joint",
    15: "FR Hip"
}

channels = [1, 2, 3, 5, 6, 12, 9, 10, 11, 13, 14, 15]

print("PCA9685 initialized.")
print("Sweeping each channel from +90 to -90 degrees (1500us +/- 1000us).")
print("Observe the physical movement. Note any joints that move opposite to expected.\n")

# Sweep parameters
center_pw = 1500.0
us_per_deg = 2000.0 / 180.0  # ~11.11 us per degree

for ch in channels:
    name = channel_names[ch]
    print(f"Channel {ch} ({name}):")
    for angle in [90, 0, -90]:
        pw = center_pw + angle * us_per_deg
        pw = max(500.0, min(2500.0, pw))
        pca.set_pulse_width(ch, pw)
        print(f"  {angle:+3d} deg -> {pw:.0f} us")
        time.sleep(1.0)
    time.sleep(0.5)

print("\nDone. For any joint that moved opposite to what you expected, mark it as reversed.")
print("Example observations:")
print('  - RL Joint moved backward when angle went +90 (needs direction=-1)')
print('  - RR Hip moved inward when angle went +90 (needs direction=-1)')


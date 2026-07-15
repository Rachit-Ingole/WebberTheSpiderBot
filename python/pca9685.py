from arduino.app_utils import Bridge

class PCA9685:
    """
    Proxy class for the PCA9685 servo driver on the MCU side.
    Buffers servo commands and sends them as a batch transaction.
    """
    def __init__(self):
        # Default initialization to standard standing centers
        self.pulses = {
            9: 1500,  # FL Foot
            10: 1700, # FL Joint
            11: 1500, # FL Hip
            13: 1500, # FR Foot
            15: 1300, # FR Joint
            14: 1400, # FR Hip
            1: 1500,  # RL Foot
            2: 1500,  # RL Joint
            3: 1600,  # RL Hip
            5: 1500,  # RR Foot
            6: 1500,  # RR Joint
            12: 1550  # RR Hip
        }

    def set_pulse_width(self, channel: int, pulse_us: float):
        """Caches the pulse width for the channel."""
        self.pulses[channel] = int(max(600, min(2400, pulse_us)))

    def write_batch(self):
        """Sends all 12 buffered servo positions to the MCU in a single RPC call."""
        try:
            Bridge.call("setAllServos",
                self.pulses[9],   # FL Foot (ch 9)
                self.pulses[10],  # FL Joint (ch 10)
                self.pulses[11],  # FL Hip (ch 11)
                self.pulses[13],  # FR Foot (ch 13)
                self.pulses[15],  # FR Joint (ch 15)
                self.pulses[14],  # FR Hip (ch 14)
                self.pulses[1],   # RL Foot (ch 1)
                self.pulses[2],   # RL Joint (ch 2)
                self.pulses[3],   # RL Hip (ch 3)
                self.pulses[5],   # RR Foot (ch 5)
                self.pulses[6],   # RR Joint (ch 6)
                self.pulses[12]   # RR Hip (ch 12)
            )
        except Exception as e:
            print(f"[PCA9685] Batch write failed: {e}")

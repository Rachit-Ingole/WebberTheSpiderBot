"""
person_follower.py — Camera-based person following for SpiderBot
================================================================
Standalone module: completely separate from locomotion code.

Architecture
------------
* Uses arduino.app_bricks.video_objectdetection.VideoObjectDetection
  with on_detect_all() to receive every frame's bounding-box dict.
* Classifies the person's horizontal position into LEFT / CENTER / RIGHT
  zone (or NONE if nobody is found).
* Keeps a minimum stand-off distance from the person using the ToF
  sensor reading exposed by main.py via a shared getter.
* Issues drive commands directly on the robot instance.  Only one
  controller is active at a time (follow_mode flag in main.py gates this).

Public surface for main.py
--------------------------
    from person_follower import PersonFollower

    follower = PersonFollower(robot, get_sensor_distance=lambda: sensor_distance)
    follower.set_active(True)    # enable following
    follower.set_active(False)   # disable following
    cmd, info = follower.latest  # (cmd_str, telemetry_dict) for UI

Detection payload shape (from VideoObjectDetection.on_detect_all)
-----------------------------------------------------------------
    {
        "person": [
            {"confidence": 0.82, "bounding_box_xyxy": (x1, y1, x2, y2)},
            ...
        ],
        ...  (other labels)
    }
    bounding_box_xyxy values are normalised 0.0–1.0 fractions of frame size.
"""

import time
import threading
from typing import Callable

from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from person_identity import PersonIdentity

# ── Tuning constants ──────────────────────────────────────────────────────────
# Horizontal zone boundaries (0 = left edge, 1 = right edge of frame)
ZONE_LEFT_END    = 0.38   # cx < 38 %  → turn left
ZONE_RIGHT_START = 0.62   # cx > 62 %  → turn right
# 38 %–62 % centre zone → walk forward

# ToF stand-off thresholds (mm)
MIN_FOLLOW_DIST = 250    # closer → stop (person too close)
MAX_FOLLOW_DIST = 1200   # no special action above this; zone command runs normally

# Seconds with no detection before switching to scan/search behaviour
NO_DETECT_TIMEOUT = 2.0

# Minimum confidence to consider a detection valid
PERSON_CONFIDENCE = 0.40

# Label string the Edge Impulse model uses for a person
DETECTION_LABELS = ("face", "person")

# Turn rate used during slow scan search (0.0–1.0)
SCAN_RATE = 0.45
FRAME_WIDTH_FALLBACK = 640


class PersonFollower:
    """Camera-based intruder/person follower, decoupled from the locomotion layer.

    The only coupling with main.py is:
      - A reference to the SpiderBot instance (to issue drive commands).
      - A callable that returns the current ToF sensor distance in mm.
    """

    def __init__(self, robot, get_sensor_distance: Callable[[], int],
                 face_recognizer=None, preview_callback=None):
        self._robot    = robot
        self._get_dist = get_sensor_distance
        self._face_recognizer = face_recognizer
        self._preview_callback = preview_callback

        self._active = False
        self._lock   = threading.Lock()

        # Telemetry exposed to the UI
        self._latest_cmd: str  = "stop"
        self._latest_info: dict = {
            "zone":       "NONE",
            "confidence": 0.0,
            "cx":         0.5,
            "dist_mm":    -1,
        }

        self._last_detect_time: float = 0.0
        self._identity = PersonIdentity(face_lost_timeout=8.0, confirm_frames=3)

        # Initialise the camera brick
        # camera_preview supplies the raw frame needed by the optional face
        # recognizer.  The identity state machine itself is model-agnostic.
        self._vod = VideoObjectDetection(
            confidence=PERSON_CONFIDENCE,
            debounce_sec=0.0,
            camera_preview=True,
        )
        # This App Lab release validates plain functions rather than bound
        # methods, so wrap the instance callback in a function.
        self._vod.on_detect_all(
            lambda detections, frame=None: self._on_detections(detections, frame)
        )
        self._vod.start()

        print("[PersonFollower] VideoObjectDetection brick started.")

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active(self, enabled: bool):
        """Enable or disable person following."""
        with self._lock:
            was_active = self._active
            self._active = enabled

        if was_active and not enabled:
            self._identity.reset()
            self._robot.stop()
            print("[PersonFollower] Disabled — robot stopped.")
        elif enabled and not was_active:
            self._last_detect_time = 0.0
            print("[PersonFollower] Enabled — scanning for person.")

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def latest(self) -> tuple:
        """Return (cmd_str, telemetry_dict) for inclusion in the status broadcast."""
        with self._lock:
            return self._latest_cmd, dict(self._latest_info)

    def shutdown(self):
        """Stop the camera brick cleanly."""
        self._vod.stop()
        print("[PersonFollower] Camera brick stopped.")

    # ── Detection callback ────────────────────────────────────────────────────

    def _on_detections(self, detections: dict, frame=None):
        """Called by VideoObjectDetection for every frame that contains any detection.

        Args:
            detections: {label: [{"confidence": float, "bounding_box_xyxy": tuple}]}
        """
        if self._preview_callback is not None and frame is not None:
            self._preview_callback(frame)

        with self._lock:
            if not self._active:
                return

        person_boxes = []
        for label in DETECTION_LABELS:
            person_boxes = detections.get(label, [])
            if person_boxes:
                break
        if not person_boxes:
            # Something was detected but not a person — treat as no-person
            if (self._last_detect_time
                    and time.time() - self._last_detect_time
                    <= self._identity.face_lost_timeout):
                self._identity.observe(track_present=True, face_visible=False)
                self._issue_command("stop", {
                    "zone": self._identity.state,
                    "confidence": 0.0,
                    "cx": 0.5,
                    "dist_mm": self._get_dist(),
                })
            else:
                self._identity.observe(track_present=False)
            self._handle_no_person()
            return

        # Choose the box with the highest confidence
        best = max(person_boxes, key=lambda b: b["confidence"])
        conf = best["confidence"]
        x1, y1, x2, y2 = best["bounding_box_xyxy"]

        # Normalize either the newer pixel-coordinate face boxes or the
        # normalized boxes returned by the older object-detection brick.
        if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
            cx = (x1 + x2) / 2.0
        else:
            width = FRAME_WIDTH_FALLBACK
            if self._face_recognizer is not None and frame is not None:
                width = self._face_recognizer.frame_width(frame) or width
            cx = (x1 + x2) / (2.0 * width)
        cx = max(0.0, min(1.0, cx))

        self._last_detect_time = time.time()
        face_visible = False
        matched_name = None
        match_confidence = 0.0
        if self._face_recognizer is not None and frame is not None:
            result = self._face_recognizer.identify(
                frame, best["bounding_box_xyxy"])
            if result is not None:
                face_visible, matched_name, match_confidence = result
        self._identity.observe(
            track_present=True,
            face_visible=face_visible,
            matched_name=matched_name,
            match_confidence=match_confidence,
        )
        self._decide_and_drive(cx, conf)

    # ── No-detection handler ──────────────────────────────────────────────────

    def _handle_no_person(self):
        """Issue a scan/search command when no person has been seen for a while."""
        info = {**info, **self._identity.telemetry()}
        with self._lock:
            if not self._active:
                return

        elapsed = (time.time() - self._last_detect_time) if self._last_detect_time else 999.0

        if elapsed >= NO_DETECT_TIMEOUT:
            self._issue_command("scan", {
                "zone":       "SCAN",
                "confidence": 0.0,
                "cx":         0.5,
                "dist_mm":    self._get_dist(),
            })

    # ── Zone → command logic ──────────────────────────────────────────────────

    def _decide_and_drive(self, cx: float, confidence: float):
        """Map the horizontal position of the detected person to a drive command.

        Stand-off rule: if the ToF sensor sees the person closer than
        MIN_FOLLOW_DIST, the robot stops regardless of which zone they are in.
        """
        dist_mm = self._get_dist()

        # Known people are not targets.  If a verified face temporarily turns
        # away, wait for re-verification instead of treating them as an
        # intruder immediately.  Only UNKNOWN/SUSPICIOUS people are followed.
        if self._identity.state in ("KNOWN", "TEMPORARILY_UNVERIFIED"):
            self._issue_command("stop", {
                "zone": self._identity.state,
                "confidence": round(confidence, 2),
                "cx": round(cx, 3),
                "dist_mm": dist_mm,
            })
            return

        # Stand-off guard — person is too close
        if 0 < dist_mm < MIN_FOLLOW_DIST:
            self._issue_command("stop", {
                "zone":       "TOO_CLOSE",
                "confidence": round(confidence, 2),
                "cx":         round(cx, 3),
                "dist_mm":    dist_mm,
            })
            return

        # Zone classification
        if cx < ZONE_LEFT_END:
            zone = "LEFT"
            cmd  = "turn_left"
        elif cx > ZONE_RIGHT_START:
            zone = "RIGHT"
            cmd  = "turn_right"
        else:
            zone = "CENTER"
            cmd  = "forward"

        self._issue_command(cmd, {
            "zone":       zone,
            "confidence": round(confidence, 2),
            "cx":         round(cx, 3),
            "dist_mm":    dist_mm,
        })

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _issue_command(self, cmd: str, info: dict):
        """Apply drive command to robot and record telemetry."""
        with self._lock:
            if not self._active:
                return
            self._latest_cmd  = cmd
            self._latest_info = info

        if cmd == "forward":
            self._robot.walkForward(speed=0.7)
        elif cmd == "turn_left":
            self._robot.turnLeft(rate=0.6)
        elif cmd == "turn_right":
            self._robot.turnRight(rate=0.6)
        elif cmd == "scan":
            # Slow rotation to search for a lost person
            self._robot.turnLeft(rate=SCAN_RATE)
        else:
            # "stop" or "TOO_CLOSE"
            self._robot.stop()

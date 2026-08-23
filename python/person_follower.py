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
from telegram_notifier import TelegramNotifier

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
        self._llm_busy = False
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
        self._telegram = TelegramNotifier()

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

    def set_llm_busy(self, busy: bool):
        """Pause heavy vision processing during LLM generation to free 100% CPU."""
        with self._lock:
            self._llm_busy = busy

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def latest(self) -> tuple:
        """Return (cmd_str, telemetry_dict) for inclusion in the status broadcast."""
        with self._lock:
            return self._latest_cmd, {**self._latest_info, **self._identity.telemetry()}

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
        if self._llm_busy:
            self._draw_and_send_preview(detections, frame)
            return
        person_boxes = []
        for label in DETECTION_LABELS:
            person_boxes = detections.get(label, [])
            if person_boxes:
                break
        if not person_boxes:
            # Something was detected but not a person — treat as no-person
            info = {
                "zone": self._identity.state,
                "confidence": 0.0,
                "cx": 0.5,
                "dist_mm": self._get_dist(),
            }
            if (self._last_detect_time
                    and time.time() - self._last_detect_time
                    <= self._identity.face_lost_timeout):
                self._identity.observe(track_present=True, face_visible=False)
                with self._lock:
                    if self._active:
                        self._issue_command("stop", info)
                    else:
                        self._latest_info = info
            else:
                self._identity.observe(track_present=False)
                with self._lock:
                    self._latest_info = info
            
            self._draw_and_send_preview(detections, frame)
            
            with self._lock:
                if not self._active:
                    return
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
        self._draw_and_send_preview(detections, frame, matched_name)
        
        with self._lock:
            if not self._active:
                # Update telemetry for WebUI even when follow mode is off
                self._latest_info = {
                    "zone": self._identity.state,
                    "confidence": round(conf, 2),
                    "cx": round(cx, 3),
                    "dist_mm": self._get_dist(),
                }
                return
                
        self._decide_and_drive(cx, conf)

    def _draw_and_send_preview(self, detections: dict, frame=None, matched_name=None):
        if self._preview_callback is None or frame is None:
            return

        # 1. Pass clean raw frame to preview callback for ORB equipment recognition
        try:
            self._preview_callback(frame)
        except Exception:
            pass

        import cv2
        import numpy as np

        # 2. Decode JPEG bytes into a numpy image array for visual UI overlays
        if isinstance(frame, (bytes, bytearray, memoryview)):
            draw_frame = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
        else:
            draw_frame = frame.copy() if hasattr(frame, 'copy') else frame

        if draw_frame is None:
            return

        h, w = draw_frame.shape[:2]
        has_intruder = False
        intruder_conf = 0.0

        # Draw bounding boxes for all detected items
        for label, boxes in detections.items():
            for box in boxes:
                x1, y1, x2, y2 = box["bounding_box_xyxy"]
                conf = box["confidence"]

                # Convert normalized coords (0.0-1.0) to pixel coordinates
                if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
                    frac_w = abs(x2 - x1)
                    frac_h = abs(y2 - y1)
                    px1, py1 = int(x1 * w), int(y1 * h)
                    px2, py2 = int(x2 * w), int(y2 * h)
                else:
                    frac_w = abs(x2 - x1) / w
                    frac_h = abs(y2 - y1) / h
                    px1, py1 = int(x1), int(y1)
                    px2, py2 = int(x2), int(y2)

                # 1. Ignore full-screen false positive boxes
                if frac_w > 0.85 and frac_h > 0.85:
                    continue

                display_label = label
                color = (34, 197, 94) # Emerald Green (BGR: 94, 197, 34) for generic objects

                if label in ("face", "person"):
                    if matched_name:
                        display_label = matched_name
                        color = (239, 68, 68) # Bright Blue (BGR: 68, 68, 239) for recognized person
                    elif label == "face":
                        if conf < 0.60:
                            continue
                        display_label = "INTRUDER"
                        color = (0, 0, 255) # Warning Red (BGR: 0, 0, 255) for intruder
                        has_intruder = True
                        intruder_conf = conf

                # Draw bounding box rectangle (thicker lines: thickness=3)
                cv2.rectangle(draw_frame, (px1, py1), (px2, py2), color, 3)

                # Label text background banner
                text = f"{display_label} {int(conf * 100)}%"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(draw_frame, (px1, py1 - th - 8), (px1 + tw + 4, py1), color, -1)

                # Overlay label text
                cv2.putText(draw_frame, text, (px1 + 2, py1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        # 2. Re-encode the image array back to JPEG bytes
        success, encoded_jpeg = cv2.imencode('.jpg', draw_frame)
        if success:
            jpeg_bytes = encoded_jpeg.tobytes()
            import base64
            from web_ui import ui
            jpeg_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
            ui.send_message("camera_preview", {"jpeg": jpeg_b64})
            
            # Send Telegram intruder alert with the drawn red bounding box image
            if has_intruder and self._telegram.enabled:
                self._telegram.send_intruder_alert(
                    confidence=intruder_conf,
                    location="Uno Q Camera",
                    image=jpeg_bytes,
                )
        else:
            self._preview_callback(frame)

    # ── No-detection handler ──────────────────────────────────────────────────

    def _handle_no_person(self):
        """Issue a scan/search command when no person has been seen for a while."""
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

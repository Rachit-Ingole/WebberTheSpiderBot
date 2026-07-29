"""Camera-only test mode: no robot, servo, ToF, or MCU control."""

import base64
import time

import cv2
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_bricks.web_ui import WebUI
from telegram_notifier import TelegramNotifier

LEFT_END = 0.38
RIGHT_START = 0.62
PREVIEW_INTERVAL = 0.25


def run_camera_test():
    print("[CameraTest] Camera-only mode is loading. No motor commands are enabled.", flush=True)
    ui = WebUI()
    last_preview = 0.0
    last_no_face_log = 0.0
    first_payload = True
    telegram = TelegramNotifier()

    try:
        from face_recognizer import FaceRecognizer
        recognizer = FaceRecognizer()
        print("[CameraTest] Face recognition is ready.", flush=True)
    except Exception as exc:
        recognizer = None
        print(f"[CameraTest] Face recognition disabled: {exc}", flush=True)

    detector = VideoObjectDetection(
        confidence=0.40,
        debounce_sec=0.0,
        camera_preview=True,
    )

    def on_frame(detections, frame=None):
        nonlocal last_preview, last_no_face_log, first_payload

        try:
            if first_payload:
                print(f"[CameraTest] First detection payload: {detections!r}", flush=True)
                first_payload = False

            # Forward the camera JPEG to the browser at a safe preview rate.
            if frame is not None:
                now = time.monotonic()
                if now - last_preview >= PREVIEW_INTERVAL:
                    try:
                        if isinstance(frame, (bytes, bytearray, memoryview)):
                            jpeg = base64.b64encode(bytes(frame)).decode("ascii")
                            ui.send_message("camera_preview", {"jpeg": jpeg})
                            last_preview = now
                    except Exception as exc:
                        print(f"[CameraTest] Preview error: {exc}", flush=True)

            raw_faces = detections.get("face", []) if isinstance(detections, dict) else []
            if isinstance(raw_faces, dict):
                faces = [raw_faces]
            elif isinstance(raw_faces, (list, tuple)):
                faces = list(raw_faces)
            else:
                faces = []

            if len(faces) == 0:
                now = time.monotonic()
                if now - last_no_face_log >= 2.0:
                    print("[CameraTest] No face visible — scanning.", flush=True)
                    last_no_face_log = now
                return

            target = max(faces, key=lambda item: item["confidence"])
            confidence = float(target["confidence"])
            x1, y1, x2, y2 = target["bounding_box_xyxy"]

            if recognizer is not None and frame is not None:
                visible, name, score = recognizer.identify(
                    frame, target["bounding_box_xyxy"])
            else:
                visible, name, score = True, None, 0.0

            if name:
                identity = f"{name} detected"
            else:
                identity = "INTRUDER detected"
                image = frame
                if not isinstance(image, (bytes, bytearray, memoryview)):
                    encoded, jpeg = cv2.imencode(".jpg", image)
                    image = jpeg.tobytes() if encoded else None
                telegram.send_intruder_alert(
                    confidence=confidence,
                    location="UNO Q camera",
                    image=image,
                )

            width = (recognizer.frame_width(frame) or 640
                     if recognizer and frame is not None else 640)
            cx = (x1 + x2) / (2.0 * width) if max(abs(x1), abs(x2)) > 1.5 else (x1 + x2) / 2.0
            if cx < LEFT_END:
                movement = "turning left"
            elif cx > RIGHT_START:
                movement = "turning right"
            else:
                movement = "centered / would move forward"

            print(
                f"[CameraTest] {identity} | face={confidence:.2f} "
                f"match={score:.3f} | cx={cx:.3f} | {movement}",
                flush=True,
            )
        except Exception as exc:
            print(f"[CameraTest] Detection callback error: {exc}", flush=True)

    # App Lab validates plain functions more reliably than bound methods.
    detector.on_detect_all(on_frame)
    # App.run() starts registered bricks. Do not call detector.start() here,
    # otherwise App Lab may start the camera twice and callbacks can stall.
    print("[CameraTest] Camera-only mode ready; App.run() will start the brick.", flush=True)

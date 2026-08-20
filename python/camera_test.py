"""Camera-only test mode: no robot, servo, ToF, or MCU control."""

import base64
import os
import threading
import time

import cv2
from arduino.app_utils import Bridge
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_bricks.web_ui import WebUI
from memory_store import MemoryStore
from telegram_notifier import TelegramNotifier
from qwen_chat import QwenChat

LEFT_END = 0.38
RIGHT_START = 0.62
PREVIEW_INTERVAL = 0.25


def run_camera_test():
    print("[CameraTest] Camera-only mode is loading. No motor commands are enabled.", flush=True)
    ui = WebUI()
    last_preview = 0.0
    last_no_face_log = 0.0
    first_payload = True
    last_memory_key = None
    telegram = TelegramNotifier()
    memory = MemoryStore()
    qwen = QwenChat(memory)
    room = os.getenv("SPIDEY_CURRENT_ROOM", "unknown")

    def ask_qwen(_sid, data):
        question = str(data.get("question", "")).strip()
        def answer_question():
            ui.send_message("chat_response", {
                "question": question,
                "answer": qwen.ask(question),
            })
        threading.Thread(target=answer_question, daemon=True).start()

    def set_display_face(_sid, data):
        states = {"idle": 0, "happy": 1, "alert": 2, "left": 3, "right": 4}
        face = str(data.get("face", "idle")).strip().lower()
        if face not in states:
            return
        Bridge.call("setDisplayState", states[face])
        ui.send_message("display_face_update", {"face": face})
        print(f"[OLED] Face set to {face}", flush=True)

    def set_room(_sid, data):
        nonlocal room
        room = str(data.get("room", "unknown")).strip().lower()[:64] or "unknown"
        print(f"[CameraTest] Manual room set to {room}", flush=True)
        ui.send_message("room_update", {"room": room})

    def save_rooms(_sid, data):
        rooms = memory.replace_rooms(data.get("rooms", []))
        ui.send_message("rooms_update", {"rooms": rooms})
        print(f"[CameraTest] Saved {len(rooms)} manual room boxes", flush=True)

    ui.on_message("set_room", set_room)
    ui.on_message("set_display_face", set_display_face)
    ui.on_message("chat_question", ask_qwen)
    ui.on_message("save_rooms", save_rooms)
    ui.send_message("rooms_update", {"rooms": memory.list_rooms()})

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
        nonlocal last_preview, last_no_face_log, first_payload, last_memory_key

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
                last_memory_key = None
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
                memory_key = name.lower()
                if memory_key != last_memory_key:
                    memory.log_event(
                        "person_seen",
                        person_name=name,
                        room=room,
                        confidence=score,
                        details={"source": "uno_q_camera"},
                    )
            else:
                identity = "INTRUDER detected"
                memory_key = "INTRUDER"
                if memory_key != last_memory_key:
                    memory.log_event(
                        "intruder_detected",
                        room=room,
                        confidence=confidence,
                        details={"source": "uno_q_camera"},
                    )
                image = frame
                if not isinstance(image, (bytes, bytearray, memoryview)):
                    encoded, jpeg = cv2.imencode(".jpg", image)
                    image = jpeg.tobytes() if encoded else None
                telegram.send_intruder_alert(
                    confidence=confidence,
                    location="UNO Q camera",
                    image=image,
                )
            last_memory_key = memory_key

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

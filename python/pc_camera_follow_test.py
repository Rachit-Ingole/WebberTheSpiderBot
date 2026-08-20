"""PC-only camera and face-following visualization.

This does not connect to the Arduino or send motor commands. It only displays
the command that the robot would use based on the face position.
"""

from collections import deque
import os
from pathlib import Path
from time import strftime

import cv2
import numpy as np

from face_recognizer import FaceRecognizer
from memory_store import MemoryStore
from telegram_notifier import TelegramNotifier


WINDOW_NAME = "Spidey PC camera test"
LEFT_LIMIT = 0.38
RIGHT_LIMIT = 0.62
PANEL_WIDTH = 360
LOG_LINES = 14


def command_for_center(center_x):
    if center_x < LEFT_LIMIT:
        return "ROTATE LEFT"
    if center_x > RIGHT_LIMIT:
        return "ROTATE RIGHT"
    return "MOVE FORWARD"


def main():
    root = Path(__file__).resolve().parent.parent
    recognizer = FaceRecognizer(project_root=root)
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise SystemExit("[PCFollowTest] Could not open the laptop camera.")

    logs = deque(maxlen=LOG_LINES)
    last_command = None
    last_status = None
    telegram = TelegramNotifier()
    memory = MemoryStore()
    room = os.getenv("SPIDEY_CURRENT_ROOM", "unknown")
    print(f"[PCFollowTest] Memory room: {room}")
    print("[PCFollowTest] Camera opened. Press q to quit.")

    while True:
        ok, frame = camera.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)
        height, width = frame.shape[:2]
        detections = recognizer._detect(frame)
        current_command = "SEARCHING"
        status = "NO FACE"
        status_color = (0, 180, 255)

        for face in detections:
            x, y, box_width, box_height = [int(round(value)) for value in face[:4]]
            _, name, score = recognizer.identify(
                frame, (x, y, x + box_width, y + box_height)
            )

            center_x = (x + box_width / 2) / width
            current_command = command_for_center(center_x)
            status = name.upper() if name else "INTRUDER"
            status_color = (0, 200, 0) if name else (0, 0, 255)
            label = f"{status}  {score:.2f}"

            cv2.rectangle(
                frame, (x, y), (x + box_width, y + box_height), status_color, 3
            )
            cv2.putText(
                frame, label, (x, max(30, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2,
            )

        if status == "INTRUDER" and last_status != "INTRUDER":
            encoded, jpeg = cv2.imencode(".jpg", frame)
            telegram.send_intruder_alert(
                location="PC webcam",
                image=jpeg.tobytes() if encoded else None,
            )
        if status != last_status and status != "NO FACE":
            if status == "INTRUDER":
                memory.log_event(
                    "intruder_detected",
                    room=room,
                    details={"source": "pc_webcam", "command": current_command},
                )
            else:
                memory.log_event(
                    "person_seen",
                    person_name=status.lower(),
                    room=room,
                    details={"source": "pc_webcam", "command": current_command},
                )
            print(f"[PCFollowTest] Memory event saved: {status} in {room}")
        last_status = status

        if current_command != last_command:
            message = f"{strftime('%H:%M:%S')}  {current_command}"
            logs.append(message)
            print(f"[PCFollowTest] {message}", flush=True)
            last_command = current_command

        # Build a right-side console panel beside the camera image.
        panel = np.full((height, PANEL_WIDTH, 3), 24, dtype=frame.dtype)
        cv2.putText(panel, "SPIDEY COMMAND CONSOLE", (20, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        cv2.putText(panel, f"STATUS: {status}", (20, 82),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
        cv2.putText(panel, current_command, (20, 125),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
        cv2.line(panel, (20, 145), (PANEL_WIDTH - 20, 145), (90, 90, 90), 1)

        for index, line in enumerate(logs):
            cv2.putText(panel, line, (20, 180 + index * 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (210, 210, 210), 1)

        display = cv2.hconcat([frame, panel])
        cv2.imshow(WINDOW_NAME, display)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

"""Bounded high-level robot task execution; never accepts raw servo commands."""

import threading
import time


class TaskExecutor:
    def __init__(self, robot, set_room, set_face, memory, ui):
        self.robot = robot
        self.set_room = set_room
        self.set_face = set_face
        self.memory = memory
        self.ui = ui
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.running = False

    def cancel(self):
        self.cancel_event.set()
        self.robot.stop()
        self._status("cancelled")

    def _status(self, status, detail=""):
        print(f"[Task] {status} {detail}".strip(), flush=True)
        self.ui.send_message("task_update", {"status": status, "detail": detail})

    def _wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel_event.is_set():
                return False
            time.sleep(0.05)
        return True

    def _validate(self, action):
        tool = str(action.get("tool", "")).strip()
        args = action.get("args") or {}
        if tool == "set_room":
            name = str(args.get("name", "")).strip()[:64]
            return (tool, {"name": name}) if name else None
        if tool == "set_face":
            face = str(args.get("face", "idle")).lower()
            return (tool, {"face": face}) if face in {"idle", "happy", "alert", "left", "right"} else None
        if tool == "move":
            direction = str(args.get("direction", "")).lower()
            try: duration = max(1.0, min(10.0, float(args.get("duration", 1))))
            except (TypeError, ValueError): return None
            return (tool, {"direction": direction, "duration": duration}) if direction in {"forward", "backward", "left", "right"} else None
        if tool == "scan_room":
            try: duration = max(1.0, min(30.0, float(args.get("duration", 8))))
            except (TypeError, ValueError): return None
            return (tool, {"duration": duration})
        if tool == "run_circles":
            try: count = max(1, min(3, int(args.get("count", 1))))
            except (TypeError, ValueError): return None
            return (tool, {"count": count})
        return None

    def execute(self, plan):
        with self.lock:
            if self.running:
                self._status("rejected", "another task is already running")
                return
            self.running = True
        self.cancel_event.clear()
        try:
            actions = plan.get("actions", []) if isinstance(plan, dict) else []
            self._status("started", plan.get("message", ""))
            for raw in actions[:12]:
                action = self._validate(raw)
                if not action:
                    self._status("rejected", "unsafe or invalid action in plan")
                    break
                tool, args = action
                self._status("running", tool)
                if tool == "set_room": self.set_room(args["name"])
                elif tool == "set_face": self.set_face(args["face"])
                elif tool == "move":
                    getattr(self.robot, {"forward":"walkForward", "backward":"walkBackward", "left":"turnLeft", "right":"turnRight"}[args["direction"]])(speed=1.0) if args["direction"] in {"forward", "backward"} else getattr(self.robot, "turnLeft" if args["direction"] == "left" else "turnRight")(rate=1.0)
                    if not self._wait(args["duration"]): break
                    self.robot.stop()
                elif tool == "scan_room":
                    self.robot.turnRight(rate=1.0)
                    if not self._wait(args["duration"]): break
                    self.robot.stop()
                    self.memory.log_event("room_scan", room="unknown", details={"duration": args["duration"]})
                elif tool == "run_circles":
                    for _ in range(args["count"]):
                        self.robot.walkForward(speed=1.0)
                        if not self._wait(1.5): break
                        self.robot.turnRight(rate=1.0)
                        if not self._wait(0.8): break
                    self.robot.stop()
                if self.cancel_event.is_set(): break
            self._status("cancelled" if self.cancel_event.is_set() else "completed")
        finally:
            self.robot.stop()
            with self.lock: self.running = False

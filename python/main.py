import time
import math
import threading
import base64
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from robot import SpiderBot
from person_follower import PersonFollower
from memory_store import MemoryStore
from qwen_chat import QwenChat
from task_executor import TaskExecutor
from bridge_lock import bridge_lock

# Set CAMERA_ENABLED to True only when the USB camera is connected.
CAMERA_ONLY = False
CAMERA_ENABLED = True

if CAMERA_ONLY:
    if CAMERA_ENABLED:
        from camera_test import run_camera_test
        run_camera_test()
    else:
        ui = WebUI()
        memory = MemoryStore()
        qwen = QwenChat(memory)
        room_state = {"value": "unknown"}

        def ask_qwen_without_camera(_sid, data):
            question = str(data.get("question", "")).strip()
            def answer_question():
                answer = qwen.ask(question)
                ui.send_message("chat_response", {"question": question, "answer": answer})
                try:
                    with bridge_lock:
                        Bridge.call("showScrollingText", answer)
                except Exception as e:
                    print(f"[OLED] Failed to scroll text: {e}", flush=True)
            threading.Thread(target=answer_question, daemon=True).start()

        ui.on_message("chat_question", ask_qwen_without_camera)

        def set_display_face_without_camera(_sid, data):
            states = {"idle": 0, "happy": 1, "alert": 2, "left": 3, "right": 4, "sleep": 6}
            face = str(data.get("face", "idle")).strip().lower()
            if face not in states:
                return
            Bridge.call("setDisplayState", states[face])
            ui.send_message("display_face_update", {"face": face})
            print(f"[OLED] Face set to {face}", flush=True)

        def set_room_without_camera(_sid, data):
            room = str(data.get("room", "unknown")).strip().lower()[:64] or "unknown"
            room_state["value"] = room
            print(f"[CameraTest] Manual room set to {room}", flush=True)
            ui.send_message("room_update", {"room": room})

        ui.on_message("set_room", set_room_without_camera)
        ui.on_message("set_display_face", set_display_face_without_camera)
        def save_rooms_without_camera(_sid, data):
            rooms = memory.replace_rooms(data.get("rooms", []))
            ui.send_message("rooms_update", {"rooms": rooms})
            print(f"[CameraTest] Saved {len(rooms)} manual room boxes", flush=True)
        ui.on_message("save_rooms", save_rooms_without_camera)
        ui.send_message("rooms_update", {"rooms": memory.list_rooms()})
        print("[CameraTest] Camera disabled. Running without camera.", flush=True)
    App.run()
    raise SystemExit

# ── Robot & UI ─────────────────────────────────────────────────────────────
robot = SpiderBot()
robot.stand()
print(f"[DEBUG][Startup] robot constructed; state={robot.state}; "
      f"camera_only={CAMERA_ONLY}; camera_enabled={CAMERA_ENABLED}", flush=True)

active_cmd = "stop"
ui = WebUI()

# ── Follow Mode ──────────────────────────────────────────────────────────────
follow_mode = False
current_room = "unknown"
memory = MemoryStore()
qwen = QwenChat(memory)
autonomy_enabled = False
llm_busy = False

# ── Voice Assistant Setup ───────────────────────────────────────────────────
ENABLE_VOICE_ASSISTANT = False
if ENABLE_VOICE_ASSISTANT:
    try:
        from voice_assistant import VoiceAssistant
        assistant = VoiceAssistant(qwen, memory)
        assistant.start()
    except Exception as exc:
        print(f"[VoiceAssistant] Failed to start service: {exc}", flush=True)

def task_set_room(name):
    global current_room
    current_room = str(name).strip().lower()[:64] or "unknown"
    ui.send_message("room_update", {"room": current_room})

def task_set_face(face):
    states = {"idle": 0, "happy": 1, "alert": 2, "left": 3, "right": 4, "sleep": 6}
    if face in states:
        with bridge_lock:
            Bridge.call("setDisplayState", states[face])
        ui.send_message("display_face_update", {"face": face})

def handle_display_face(sid, data):
    states = {"idle": 0, "happy": 1, "alert": 2, "left": 3, "right": 4, "sleep": 6}
    face = str(data.get("face", "idle")).strip().lower()
    if face not in states:
        return
    with bridge_lock:
        Bridge.call("setDisplayState", states[face])
    ui.send_message("display_face_update", {"face": face})
    print(f"[OLED] Face set to {face}")

def handle_chat_question(sid, data):
    global llm_busy
    question = str(data.get("question", "")).strip()
    def answer_question():
        global llm_busy
        llm_busy = True
        if follower is not None:
            follower.set_llm_busy(True)
        try:
            answer = qwen.ask(question)
        finally:
            llm_busy = False
            if follower is not None:
                follower.set_llm_busy(False)
        ui.send_message("chat_response", {"question": question, "answer": answer})
        try:
            with bridge_lock:
                Bridge.call("showScrollingText", answer)
        except Exception as e:
            print(f"[OLED] Failed to scroll text: {e}", flush=True)
    threading.Thread(target=answer_question, daemon=True).start()

def handle_toggle_autonomy(sid, data):
    global autonomy_enabled
    autonomy_enabled = not autonomy_enabled
    print(f"[Task] autonomy {'enabled' if autonomy_enabled else 'disabled'}", flush=True)
    ui.send_message("autonomy_update", {"enabled": autonomy_enabled})

def handle_task_request(sid, data):
    global llm_busy
    request = str(data.get("task", "")).strip()
    if not request:
        return
    def plan_and_run():
        global llm_busy
        llm_busy = True
        if follower is not None:
            follower.set_llm_busy(True)
        try:
            plan = qwen.plan_task(request)
        finally:
            llm_busy = False
            if follower is not None:
                follower.set_llm_busy(False)
        ui.send_message("task_plan", plan)
        if not autonomy_enabled:
            ui.send_message("task_update", {"status": "waiting", "detail": "Enable Autonomous Tasks to run this plan."})
            return
        executor.execute(plan)
    threading.Thread(target=plan_and_run, daemon=True).start()

def handle_cancel_task(sid, data):
    executor.cancel()
# PersonFollower is instantiated lazily after sensor_distance is defined below

_last_preview_sent = 0.0
CAMERA_PREVIEW_INTERVAL = 1.0   # 1 FPS reduces CPU vision workload by 75%, matching Spidey's gait stride cycle

def send_camera_preview(frame):
    """Forward throttled JPEG preview frames to the browser UI."""
    global _last_preview_sent
    global detected_equipment_name, detected_equipment_matches, detected_equipment_time
    global last_logged_equipment, last_logged_time
    global llm_busy
    now = time.monotonic()
    if now - _last_preview_sent < CAMERA_PREVIEW_INTERVAL:
        return
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        return
    _last_preview_sent = now

    # When LLM is generating, skip heavy equipment recognition to free 100% CPU
    if llm_busy:
        try:
            jpeg_b64 = base64.b64encode(bytes(frame)).decode("ascii")
            ui.send_message("camera_preview", {"jpeg": jpeg_b64})
        except Exception:
            pass
        return

    draw_image = None
    # Run equipment recognition on the throttled preview frames
    if equip_recognizer is not None:
        try:
            name, matches = equip_recognizer.match_frame(frame)
            if name is not None:
                detected_equipment_name = name
                detected_equipment_matches = matches
                detected_equipment_time = time.time()
                print(f"[EquipmentDetector] Found: {name} ({matches} matches)", flush=True)

            active_name = name or (detected_equipment_name if time.time() - detected_equipment_time < 3.0 and detected_equipment_name != "NONE" else None)
            active_matches = matches if name is not None else detected_equipment_matches

            if active_name is not None:
                # Decode frame to draw on it
                import numpy as np
                import cv2
                draw_image = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
                if draw_image is not None:
                    h, w = draw_image.shape[:2]
                    # Draw a nice thick orange border around the center 60% target area (BGR: 0, 165, 255)
                    color = (0, 165, 255)
                    ry1, rx1 = int(h * 0.2), int(w * 0.2)
                    ry2, rx2 = int(h * 0.8), int(w * 0.8)
                    cv2.rectangle(draw_image, (rx1, ry1), (rx2, ry2), color, 3)
                    
                    # Label text background banner
                    text = f"EQUIPMENT: {active_name.upper()} ({active_matches} matches)"
                    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                    cv2.rectangle(draw_image, (rx1, ry1 - th - 8), (rx1 + tw + 4, ry1), color, -1)
                    cv2.putText(draw_image, text, (rx1 + 2, ry1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
                
                # Log detection to database if it's a new item or 10 seconds have elapsed
                if name != last_logged_equipment or (time.time() - last_logged_time > 10.0):
                    memory.log_event(
                        event_type="equipment_seen",
                        person_name=name,
                        room=current_room,
                        confidence=float(matches),
                        details={"matches": matches}
                    )
                    last_logged_equipment = name
                    last_logged_time = time.time()
                    print(f"[EquipmentDetector] Logged equipment_seen to database: {name}", flush=True)
        except Exception as e:
            print(f"[EquipmentDetector] Error during match: {e}", flush=True)

    try:
        # Re-encode if we drew on the image
        if draw_image is not None:
            success, encoded_jpeg = cv2.imencode('.jpg', draw_image)
            if success:
                frame = encoded_jpeg.tobytes()

        jpeg_b64 = base64.b64encode(bytes(frame)).decode("ascii")
        ui.send_message("camera_preview", {"jpeg": jpeg_b64})
    except Exception:
        pass

# ── Sensor globals ──────────────────────────────────────────────────────────
sensor_distance = -1
sensor_ax = 0
sensor_ay = 0
sensor_az = 0
sensor_gx = 0
sensor_gy = 0
sensor_gz = 0
sensor_temp = 0.0

# ── Equipment Detector Globals ──────────────────────────────────────────────
detected_equipment_name = "NONE"
detected_equipment_matches = 0
detected_equipment_time = 0.0
last_logged_equipment = "NONE"
last_logged_time = 0.0

# ── Safety ──────────────────────────────────────────────────────────────────
OBSTACLE_THRESHOLD = 150          # mm — auto-avoidance trigger distance
_obstacle_cooldown = 0.0          # timestamp until avoidance is suppressed

# ── Auto Mode ───────────────────────────────────────────────────────────────
import random
auto_mode    = False              # True = autonomous patrol + avoidance
_auto_state  = "WALK"             # "WALK" | "AVOID"
_auto_avoid_until = 0.0           # when the current avoidance turn ends

# ── Occupancy map ───────────────────────────────────────────────────────────
MAP_CELLS   = 100                 # grid is MAP_CELLS x MAP_CELLS
CELL_SIZE_M = 0.05                # metres per cell (5 cm)
_map_grid   = [[0] * MAP_CELLS for _ in range(MAP_CELLS)]  # 0=free, 1=occupied

# Robot pose in map-space (continuous metres, angle in radians)
_pose_x   = (MAP_CELLS // 2) * CELL_SIZE_M   # start at centre
_pose_y   = (MAP_CELLS // 2) * CELL_SIZE_M
_pose_th  = 0.0                               # heading: 0 = pointing +x

# Dead-reckoning constants (empirically tuned)
# stride_amp is in us; 800 us approx 4 cm stride -> 0.00005 m/us
DIST_PER_US_PER_TICK = 0.00005 * 0.2   # metres moved per us of stride_amp per 0.2s tick
# Gyro-Z raw LSB @ +/-250 deg/s full-scale: 131 LSB per deg/s
GYRO_SCALE_RAD = math.radians(1.0 / 131.0)

_pose_lock = threading.Lock()

# ── Map helpers ─────────────────────────────────────────────────────────────
def _clamp_cell(v):
    return max(0, min(MAP_CELLS - 1, int(v)))

def _update_pose(dt: float):
    """Integrate dead-reckoning from current gait state and gyro-Z."""
    global _pose_x, _pose_y, _pose_th
    if robot.state != "WALK":
        return
    gz_rad_per_s = sensor_gz * GYRO_SCALE_RAD
    with _pose_lock:
        _pose_th += gz_rad_per_s * dt
        dist = robot.stride_amp * DIST_PER_US_PER_TICK * robot.vx
        _pose_x += dist * math.cos(_pose_th)
        _pose_y += dist * math.sin(_pose_th)
        _pose_x = max(0.0, min((MAP_CELLS - 1) * CELL_SIZE_M, _pose_x))
        _pose_y = max(0.0, min((MAP_CELLS - 1) * CELL_SIZE_M, _pose_y))

def _mark_obstacle():
    """Project the VL53L0X reading onto the grid and mark it occupied."""
    if sensor_distance <= 0:
        return
    with _pose_lock:
        ox = _pose_x + sensor_distance * 0.001 * math.cos(_pose_th)
        oy = _pose_y + sensor_distance * 0.001 * math.sin(_pose_th)
        cx = _clamp_cell(ox / CELL_SIZE_M)
        cy = _clamp_cell(oy / CELL_SIZE_M)
        _map_grid[cy][cx] = 1

def _serialise_map():
    """Return compact map data: list of occupied (col, row) pairs + robot cell."""
    with _pose_lock:
        rx = _clamp_cell(_pose_x / CELL_SIZE_M)
        ry = _clamp_cell(_pose_y / CELL_SIZE_M)
        occupied = [[c, r] for r, row in enumerate(_map_grid)
                    for c, v in enumerate(row) if v == 1]
    return {"occupied": occupied, "robot": [rx, ry], "cells": MAP_CELLS}

def _reset_map():
    """Clear the occupancy grid and reset pose to centre."""
    global _pose_x, _pose_y, _pose_th
    with _pose_lock:
        for r in range(MAP_CELLS):
            for c in range(MAP_CELLS):
                _map_grid[r][c] = 0
        _pose_x = (MAP_CELLS // 2) * CELL_SIZE_M
        _pose_y = (MAP_CELLS // 2) * CELL_SIZE_M
        _pose_th = 0.0

# ── Status broadcast ────────────────────────────────────────────────────────
def broadcast_status_packet():
    """Sends current state and sensor/pose data to the Web UI."""
    try:
        with _pose_lock:
            px, py, pth = _pose_x, _pose_y, _pose_th
        follower_cmd, follower_info = follower.latest if follower else ("stop", {})
        ui.send_message("status_update", {
            "state":           robot.state,
            "active_cmd":      active_cmd,
            "auto_mode":       auto_mode,
            "follow_mode":     follow_mode,
            "current_room":    current_room,
            "body_height":     robot.body_height,
            "step_height":     robot.step_height,
            "stride_amp":      robot.stride_amp,
            "cycle_time":      robot.cycle_time,
            "sensor_distance": sensor_distance,
            "sensor_ax":       sensor_ax,
            "sensor_ay":       sensor_ay,
            "sensor_az":       sensor_az,
            "sensor_gx":       sensor_gx,
            "sensor_gy":       sensor_gy,
            "sensor_gz":       sensor_gz,
            "sensor_temp":     sensor_temp,
            "pose_x":          round(px, 3),
            "pose_y":          round(py, 3),
            "pose_th":         round(math.degrees(pth), 1),
            # Follow-mode telemetry
            "follower_zone":   follower_info.get("zone",       "NONE"),
            "follower_conf":   follower_info.get("confidence", 0.0),
            "follower_cx":     follower_info.get("cx",         0.5),
            "follower_identity": follower_info.get("identity", "NO_PERSON"),
            "follower_identity_name": follower_info.get("identity_name", ""),
            "follower_face_visible": follower_info.get("face_visible", False),
            # Equipment recognition telemetry
            "equipment_name": detected_equipment_name if (time.time() - detected_equipment_time < 3.0) else "NONE",
            "equipment_matches": detected_equipment_matches if (time.time() - detected_equipment_time < 3.0) else 0,
        })
    except Exception:
        pass

# ── Control callbacks ───────────────────────────────────────────────────────
def handle_control(sid, data):
    global active_cmd, auto_mode, follow_mode
    cmd = data.get('cmd', '')
    print(f"[DEBUG][Control] received cmd={cmd!r} state_before={robot.state} "
          f"active_before={active_cmd}", flush=True)

    # Any manual command disables auto mode and follow mode
    if cmd in ('forward', 'backward', 'left', 'right', 'stop'):
        robot.stabilize_enabled = False
        auto_mode = False
        if follow_mode:
            follow_mode = False
            if follower is not None:
                follower.set_active(False)

    if cmd == 'forward':
        robot.walkForward(speed=1.0)
        active_cmd = 'forward'
    elif cmd == 'backward':
        robot.walkBackward(speed=1.0)
        active_cmd = 'backward'
    elif cmd == 'left':
        robot.turnLeft(rate=1.0)
        active_cmd = 'left'
    elif cmd == 'right':
        robot.turnRight(rate=1.0)
        active_cmd = 'right'
    elif cmd == 'stop':
        robot.stop()
        active_cmd = 'stop'
    elif cmd == 'stabilize':
        robot.stabilize()
        if robot.stabilize_enabled:
            robot.stand()
            active_cmd = 'stabilize'
        else:
            robot.stand()
            active_cmd = 'stop'
    elif cmd == 'reset_map':
        _reset_map()

    broadcast_status_packet()
    print(f"[DEBUG][Control] completed cmd={cmd!r} state_after={robot.state} "
          f"active_after={active_cmd} vx={robot.vx} omega={robot.omega}", flush=True)

def handle_toggle_auto(sid, data):
    """Toggle autonomous patrol mode on/off."""
    global auto_mode, active_cmd, _auto_state, follow_mode
    auto_mode = not auto_mode
    if auto_mode:
        # Disable follow mode when auto mode activates
        if follow_mode:
            follow_mode = False
            if follower is not None:
                follower.set_active(False)
        # Start patrolling immediately
        _auto_state = "WALK"
        robot.walkForward(speed=1.0)
        active_cmd = "forward"
        print("[SpiderBot] Auto mode ON — patrol started")
    else:
        robot.stop()
        active_cmd = "stop"
        print("[SpiderBot] Auto mode OFF")
    broadcast_status_packet()

def handle_toggle_follow(sid, data):
    """Toggle camera-based person follow mode on/off."""
    global follow_mode, auto_mode, active_cmd
    if not CAMERA_ENABLED or follower is None:
        follow_mode = False
        print("[SpiderBot] Follow mode unavailable: camera is disabled.")
        broadcast_status_packet()
        return
    follow_mode = not follow_mode
    if follow_mode:
        # Follow mode is mutually exclusive with auto mode
        if auto_mode:
            auto_mode = False
        follower.set_active(True)
        active_cmd = "follow"
        print("[SpiderBot] Follow mode ON")
    else:
        follower.set_active(False)
        robot.stop()
        active_cmd = "stop"
        print("[SpiderBot] Follow mode OFF")
    broadcast_status_packet()

def handle_set_room(sid, data):
    """Set the manually selected room used for future memory events."""
    global current_room
    value = str(data.get("room", "unknown")).strip().lower()
    current_room = value[:64] or "unknown"
    print(f"[Spidey] Manual room set to {current_room}")
    ui.send_message("room_update", {"room": current_room})
    broadcast_status_packet()

def handle_save_rooms(sid, data):
    rooms = memory.replace_rooms(data.get("rooms", []))
    ui.send_message("rooms_update", {"rooms": rooms})
    print(f"[Spidey] Saved {len(rooms)} manual room boxes")

def handle_adjust(sid, data):
    global OBSTACLE_THRESHOLD
    body_height        = data.get('body_height')
    step_height        = data.get('step_height')
    stride_amp         = data.get('stride_amp')
    cycle_time         = data.get('cycle_time')
    obstacle_threshold = data.get('obstacle_threshold')

    if body_height        is not None: robot.body_height       = float(body_height)
    if step_height        is not None: robot.step_height       = float(step_height)
    if stride_amp         is not None: robot.stride_amp        = float(stride_amp)
    if cycle_time         is not None: robot.cycle_time        = float(cycle_time)
    if obstacle_threshold is not None: OBSTACLE_THRESHOLD      = float(obstacle_threshold)

    print(f"[SpiderBot] height={robot.body_height} step={robot.step_height} "
          f"stride={robot.stride_amp} cycle={robot.cycle_time} "
          f"obstacle={OBSTACLE_THRESHOLD}")
    broadcast_status_packet()

ui.on_message("control_cmd",   handle_control)
ui.on_message("adjust_params", handle_adjust)
ui.on_message("toggle_auto",   handle_toggle_auto)
ui.on_message("toggle_follow", handle_toggle_follow)
ui.on_message("set_room",      handle_set_room)
ui.on_message("set_display_face", handle_display_face)
ui.on_message("chat_question", handle_chat_question)
ui.on_message("toggle_autonomy", handle_toggle_autonomy)
ui.on_message("task_request", handle_task_request)
ui.on_message("cancel_task", handle_cancel_task)
ui.on_message("save_rooms",    handle_save_rooms)
ui.send_message("rooms_update", {"rooms": memory.list_rooms()})

executor = TaskExecutor(robot, task_set_room, task_set_face, memory, ui)

# ── PersonFollower (initialised here so sensor_distance lambda is valid) ──────
follower = None
equip_recognizer = None
if CAMERA_ENABLED:
    try:
        from face_recognizer import FaceRecognizer
        face_recognizer = FaceRecognizer()
    except Exception as exc:
        face_recognizer = None
        print(f"[FaceRecognizer] Disabled: {exc}")
    try:
        from equipment_recognizer import EquipmentRecognizer
        equip_recognizer = EquipmentRecognizer()
    except Exception as exc:
        equip_recognizer = None
        print(f"[EquipmentRecognizer] Disabled: {exc}")
    follower = PersonFollower(
        robot,
        get_sensor_distance=lambda: sensor_distance,
        face_recognizer=face_recognizer,
        preview_callback=send_camera_preview,
    )
else:
    print("[FaceRecognizer] Disabled: camera is disabled.")

# ── Sensor + mapping loop (5 Hz) ────────────────────────────────────────────
_map_broadcast_counter = 0   # broadcast map every 5 ticks (1 Hz)

def broadcast_loop():
    global sensor_distance, sensor_ax, sensor_ay, sensor_az
    global sensor_gx, sensor_gy, sensor_gz, sensor_temp
    global active_cmd, _obstacle_cooldown, _map_broadcast_counter

    from arduino.app_utils import Bridge
    last_tick = time.time()
    last_sensor_error_log = 0.0

    while True:
        now = time.time()
        dt  = now - last_tick
        last_tick = now

        try:
            with bridge_lock:
                data_str = Bridge.call("readSensors")
            if data_str:
                parts = data_str.split(',')
                if len(parts) >= 7:
                    sensor_distance = int(parts[0])
                    sensor_ax       = int(parts[1])
                    sensor_ay       = int(parts[2])
                    sensor_az       = int(parts[3])
                    sensor_gx       = int(parts[4])
                    sensor_gy       = int(parts[5])
                    sensor_gz       = int(parts[6])
                    if len(parts) >= 8:
                        # MPU6050 Temp offset: T = (raw / 340.0) + 36.53
                        raw_t = int(parts[7])
                        sensor_temp = round((raw_t / 340.0) + 36.53, 1)
                    else:
                        sensor_temp = 0.0

            # Dead-reckoning pose update
            _update_pose(dt)

            # Feed latest IMU into robot for stabilization
            robot.set_imu(sensor_ax, sensor_ay, sensor_az)

            # Mark obstacle reading on map
            _mark_obstacle()

            # ── Auto Mode state machine ───────────────────────────────────
            if auto_mode:
                if _auto_state == "WALK":
                    # Obstacle detected → start avoidance turn
                    if (sensor_distance > 0
                            and sensor_distance < OBSTACLE_THRESHOLD
                            and now > _obstacle_cooldown):
                        # Random turn: left or right, 0.6–1.4 s
                        turn_dur = random.uniform(0.6, 1.4)
                        if random.choice([True, False]):
                            robot.turnLeft(rate=1.0)
                            active_cmd = "left"
                        else:
                            robot.turnRight(rate=1.0)
                            active_cmd = "right"
                        _auto_avoid_until = now + turn_dur
                        _obstacle_cooldown = now + turn_dur + 0.3
                        _auto_state = "AVOID"
                        try:
                            ui.send_message("obstacle_alert", {})
                        except Exception:
                            pass

                elif _auto_state == "AVOID":
                    # Turn finished → resume forward walk
                    if now >= _auto_avoid_until:
                        robot.walkForward(speed=1.0)
                        active_cmd = "forward"
                        _auto_state = "WALK"

            # ── Manual obstacle safety (auto mode OFF) ────────────────────
            else:
                if (sensor_distance > 0
                        and sensor_distance < OBSTACLE_THRESHOLD
                        and robot.state == "WALK"
                        and now > _obstacle_cooldown):
                    robot.stop()
                    active_cmd = "stop"
                    _obstacle_cooldown = now + 1.0
                    try:
                        ui.send_message("obstacle_alert", {})
                    except Exception:
                        pass

        except Exception as exc:
            if now - last_sensor_error_log >= 2.0:
                last_sensor_error_log = now
                print(f"[DEBUG][Sensors] read/update failed: {exc}", flush=True)

        broadcast_status_packet()

        # Broadcast map at 1 Hz
        _map_broadcast_counter += 1
        if _map_broadcast_counter >= 5:
            _map_broadcast_counter = 0
            try:
                ui.send_message("map_update", _serialise_map())
            except Exception:
                pass

        time.sleep(0.2)   # 5 Hz

threading.Thread(target=broadcast_loop, daemon=True).start()
print("SpiderBot Phase 3 ready -- mapping + obstacle avoidance active.")

# ── Main loop ───────────────────────────────────────────────────────────────
def loop():
    robot.update()
    time.sleep(0.02)   # ~50 Hz

App.run(user_loop=loop)

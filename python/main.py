import time
import math
import threading
import base64
from arduino.app_utils import App
from arduino.app_bricks.web_ui import WebUI
from robot import SpiderBot
from person_follower import PersonFollower

# ═══════════════════════════════════════════════════════════════════════════
# CAMERA-ONLY TEST SWITCH
# True  = camera, face recognition, preview, and console logs only.
# False = full SpiderBot locomotion application below.
# Set this back to False when the camera test is complete.
# ═══════════════════════════════════════════════════════════════════════════
CAMERA_ONLY = True

# Set False when the USB camera is unavailable. The robot, OLED, WebUI,
# sensors, and manual controls still run; only face detection/follow mode is
# disabled. Set True again when a working camera is connected.
CAMERA_ENABLED = False

if CAMERA_ONLY:
    if CAMERA_ENABLED:
        from camera_test import run_camera_test
        run_camera_test()
    else:
        ui = WebUI()
        print("[CameraTest] Camera disabled. Running without camera.", flush=True)
    App.run()
    raise SystemExit

# ── Robot & UI ─────────────────────────────────────────────────────────────
robot = SpiderBot()
robot.stand()

active_cmd = "stop"
ui = WebUI()

# ── Follow Mode ──────────────────────────────────────────────────────────────
follow_mode = False
# PersonFollower is instantiated lazily after sensor_distance is defined below

_last_preview_sent = 0.0
CAMERA_PREVIEW_INTERVAL = 0.25  # 4 FPS keeps the UI responsive and lightweight

def send_camera_preview(frame):
    """Forward throttled JPEG preview frames to the browser UI."""
    global _last_preview_sent
    now = time.monotonic()
    if now - _last_preview_sent < CAMERA_PREVIEW_INTERVAL:
        return
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        return
    _last_preview_sent = now
    try:
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
        })
    except Exception:
        pass

# ── Control callbacks ───────────────────────────────────────────────────────
def handle_control(sid, data):
    global active_cmd, auto_mode, follow_mode
    cmd = data.get('cmd', '')

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

# ── PersonFollower (initialised here so sensor_distance lambda is valid) ──────
follower = None
if CAMERA_ENABLED:
    try:
        from face_recognizer import FaceRecognizer
        face_recognizer = FaceRecognizer()
    except Exception as exc:
        face_recognizer = None
        print(f"[FaceRecognizer] Disabled: {exc}")
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
    global sensor_gx, sensor_gy, sensor_gz
    global active_cmd, _obstacle_cooldown, _map_broadcast_counter

    from arduino.app_utils import Bridge
    last_tick = time.time()

    while True:
        now = time.time()
        dt  = now - last_tick
        last_tick = now

        try:
            data_str = Bridge.call("readSensors")
            if data_str:
                parts = data_str.split(',')
                if len(parts) == 7:
                    sensor_distance = int(parts[0])
                    sensor_ax       = int(parts[1])
                    sensor_ay       = int(parts[2])
                    sensor_az       = int(parts[3])
                    sensor_gx       = int(parts[4])
                    sensor_gy       = int(parts[5])
                    sensor_gz       = int(parts[6])

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

        except Exception:
            pass

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

# ============================================================
#  LumiNet AI — OpenCV Vehicle Detector v2.0
#  Uses laptop webcam to count vehicles via background
#  subtraction + contour detection + optional YOLO.
#  Publishes traffic_density (0–100) to MQTT + Socket.IO
#  Runs as a standalone process alongside the backend.
# ============================================================

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import socketio
import json
import time
import threading
import argparse
import os
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────
MQTT_BROKER      = "localhost"
MQTT_PORT        = 1883
TRAFFIC_TOPIC    = "lights/traffic"
BACKEND_SOCKET   = "http://localhost:5000"

CAMERA_INDEX     = 0          # 0 = default webcam
FRAME_WIDTH      = 640
FRAME_HEIGHT     = 480
PROCESS_FPS      = 10         # process at 10 fps to save CPU

# Detection tuning
MIN_CONTOUR_AREA = 800        # px² — ignore smaller blobs (noise)
MAX_CONTOUR_AREA = 80000      # ignore full-frame blobs
VEHICLE_SCALE    = 3.3        # contour count × scale → density 0-100
SMOOTH_ALPHA     = 0.3        # EMA smoothing factor

# Which node each camera zone maps to (for multi-camera later)
ZONE_NODE_MAP = {0: "LN-100", 1: "LN-101", 2: "LN-102"}

# Optional YOLO (set paths if available)
YOLO_WEIGHTS = "yolov4-tiny.weights"
YOLO_CONFIG  = "yolov4-tiny.cfg"
YOLO_CLASSES = ["car", "motorbike", "bus", "truck", "bicycle"]
USE_YOLO     = os.path.exists(YOLO_WEIGHTS) and os.path.exists(YOLO_CONFIG)

# ── STATE ────────────────────────────────────────────────────
state = {
    "running":          True,
    "vehicle_count":    0,
    "traffic_density":  0.0,   # 0–100
    "smoothed_density": 0.0,
    "fps":              0.0,
    "frame":            None,
    "annotated":        None,
    "mqtt_ok":          False,
    "socket_ok":        False,
    "detection_mode":   "YOLO" if USE_YOLO else "MOG2",
}

# ── MQTT ─────────────────────────────────────────────────────
mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="OpenCV_Detector"
)

def on_mqtt_connect(client, userdata, flags, rc, props):
    state["mqtt_ok"] = rc == 0
    print(f"[MQTT] {'✅ Connected' if rc==0 else f'❌ rc={rc}'}")

mqtt_client.on_connect = on_mqtt_connect

def mqtt_connect_loop():
    while state["running"]:
        try:
            if not mqtt_client.is_connected():
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                mqtt_client.loop_start()
        except Exception as e:
            print(f"[MQTT] Retry in 5s: {e}")
        time.sleep(5)

# ── SOCKET.IO ────────────────────────────────────────────────
sio = socketio.Client(logger=False, engineio_logger=False)

@sio.event
def connect():
    state["socket_ok"] = True
    print("[Socket] ✅ Connected to backend")

@sio.event
def disconnect():
    state["socket_ok"] = False

def socket_connect_loop():
    while state["running"]:
        try:
            if not sio.connected:
                sio.connect(BACKEND_SOCKET)
        except Exception:
            pass
        time.sleep(5)

# ── YOLO DETECTOR ────────────────────────────────────────────
yolo_net    = None
yolo_layers = None

def load_yolo():
    global yolo_net, yolo_layers, USE_YOLO
    try:
        yolo_net = cv2.dnn.readNet(YOLO_WEIGHTS, YOLO_CONFIG)
        layer_names  = yolo_net.getLayerNames()
        yolo_layers  = [layer_names[i - 1] for i in yolo_net.getUnconnectedOutLayers()]
        print("[YOLO] ✅ Model loaded")
        USE_YOLO = True
    except Exception as e:
        print(f"[YOLO] ❌ Load failed ({e}) — falling back to MOG2")
        USE_YOLO = False
        state["detection_mode"] = "MOG2"

def detect_yolo(frame):
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1/255.0, (416, 416), swapRB=True, crop=False)
    yolo_net.setInput(blob)
    outs = yolo_net.forward(yolo_layers)

    boxes, confidences = [], []
    for out in outs:
        for det in out:
            scores = det[5:]
            class_id = np.argmax(scores)
            conf = float(scores[class_id])
            if conf > 0.4 and class_id < len(YOLO_CLASSES):
                cx, cy, bw, bh = (det[:4] * np.array([w, h, w, h])).astype(int)
                x, y = cx - bw//2, cy - bh//2
                boxes.append([x, y, bw, bh])
                confidences.append(conf)

    indices = cv2.dnn.NMSBoxes(boxes, confidences, 0.4, 0.3)
    count = 0
    annotated = frame.copy()
    if len(indices) > 0:
        for i in indices.flatten():
            x, y, bw, bh = boxes[i]
            cv2.rectangle(annotated, (x,y), (x+bw, y+bh), (0,255,136), 2)
            cv2.putText(annotated, f"{YOLO_CLASSES[0]} {confidences[i]:.2f}",
                        (x, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,136), 1)
            count += 1
    return count, annotated

# ── MOG2 BACKGROUND SUBTRACTOR ───────────────────────────────
mog2 = cv2.createBackgroundSubtractorMOG2(
    history=300, varThreshold=40, detectShadows=True
)
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

def detect_mog2(frame):
    gray   = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur   = cv2.GaussianBlur(gray, (5,5), 0)
    mask   = mog2.apply(blur)
    # Remove shadows (127) keep foreground (255)
    _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    count = 0
    annotated = frame.copy()

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if MIN_CONTOUR_AREA < area < MAX_CONTOUR_AREA:
            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / max(h, 1)
            # Vehicles are wider than tall (aspect > 0.5)
            if 0.4 < aspect < 5.0:
                cv2.rectangle(annotated, (x,y), (x+w, y+h), (0,255,136), 2)
                cv2.putText(annotated, f"Vehicle", (x, y-6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0,245,255), 1)
                count += 1

    # Show mask inset (top-right corner)
    mask_rgb  = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    small     = cv2.resize(mask_rgb, (160, 120))
    annotated[8:128, annotated.shape[1]-168:annotated.shape[1]-8] = small

    return count, annotated

# ── OVERLAY HUD ──────────────────────────────────────────────
def draw_hud(frame, count, density, fps, mode):
    h, w = frame.shape[:2]
    overlay = frame.copy()

    # Top bar
    cv2.rectangle(overlay, (0,0), (w, 52), (5,13,23), -1)
    cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)

    cv2.putText(frame, "LumiNet AI  |  Vehicle Detector",
                (12, 18), cv2.FONT_HERSHEY_DUPLEX, 0.55, (0,245,255), 1)
    cv2.putText(frame, datetime.now().strftime("%H:%M:%S"),
                (w-90, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100,200,255), 1)
    cv2.putText(frame, f"Mode: {mode}  |  FPS: {fps:.1f}",
                (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (100,180,100), 1)

    # Bottom bar
    cv2.rectangle(frame, (0, h-52), (w, h), (5,13,23), -1)
    density_color = (0,255,136) if density < 40 else (0,215,255) if density < 70 else (0,80,255)
    cv2.putText(frame, f"Vehicles: {count}",
                (12, h-30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
    cv2.putText(frame, f"Traffic Density: {density:.0f}%",
                (12, h-10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, density_color, 1)

    # Density bar
    bar_x, bar_y, bar_w, bar_h = w-220, h-40, 200, 16
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_w, bar_y+bar_h), (30,40,50), -1)
    fill = int(bar_w * density / 100)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x+fill, bar_y+bar_h), density_color, -1)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x+bar_w, bar_y+bar_h), (0,245,255), 1)
    cv2.putText(frame, f"{density:.0f}%", (bar_x+bar_w+6, bar_y+13),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, density_color, 1)

    return frame

# ── PUBLISH ──────────────────────────────────────────────────
def publish_traffic(density):
    payload = json.dumps({
        "traffic_density": round(density, 1),
        "vehicle_count":   state["vehicle_count"],
        "timestamp":       datetime.now().isoformat(),
        "source":          "opencv",
        "mode":            state["detection_mode"],
    })
    if state["mqtt_ok"]:
        try: mqtt_client.publish(TRAFFIC_TOPIC, payload)
        except: pass
    if state["socket_ok"]:
        try: sio.emit("traffic_update", json.loads(payload))
        except: pass

# ── MAIN DETECTION LOOP ──────────────────────────────────────
def detection_loop():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, PROCESS_FPS)

    if not cap.isOpened():
        print("[Camera] ❌ Cannot open camera index", CAMERA_INDEX)
        state["running"] = False
        return

    print(f"[Camera] ✅ Opened — {FRAME_WIDTH}×{FRAME_HEIGHT} @ {PROCESS_FPS}fps")
    print(f"[Detect] Mode: {state['detection_mode']}")

    fps_timer   = time.time()
    frame_count = 0
    last_pub    = time.time()

    while state["running"]:
        ret, frame = cap.read()
        if not ret:
            print("[Camera] Frame read failed — retrying")
            time.sleep(0.1)
            continue

        frame_count += 1
        elapsed = time.time() - fps_timer
        if elapsed >= 1.0:
            state["fps"] = frame_count / elapsed
            fps_timer, frame_count = time.time(), 0

        # Detect
        if USE_YOLO and state["detection_mode"] == "YOLO":
            count, annotated = detect_yolo(frame)
        else:
            count, annotated = detect_mog2(frame)

        # Smooth density with EMA
        raw_density = min(100.0, count * VEHICLE_SCALE * 10)
        state["smoothed_density"] = (
            SMOOTH_ALPHA * raw_density +
            (1 - SMOOTH_ALPHA) * state["smoothed_density"]
        )
        state["vehicle_count"]   = count
        state["traffic_density"] = round(state["smoothed_density"], 1)

        # Draw HUD
        annotated = draw_hud(
            annotated, count,
            state["traffic_density"],
            state["fps"],
            state["detection_mode"]
        )
        state["annotated"] = annotated
        state["frame"]     = frame

        # Publish every 2 s
        if time.time() - last_pub >= 2.0:
            publish_traffic(state["traffic_density"])
            last_pub = time.time()

        # Display
        cv2.imshow("LumiNet AI — Vehicle Detector (Q to quit)", annotated)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            state["running"] = False
        elif key == ord('m'):
            # Toggle mode
            state["detection_mode"] = "MOG2" if state["detection_mode"]=="YOLO" else "YOLO"
            print(f"[Detect] Switched to {state['detection_mode']}")

        time.sleep(max(0, 1/PROCESS_FPS - 0.005))

    cap.release()
    cv2.destroyAllWindows()
    print("[Camera] Stopped")

# ── ENTRY POINT ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LumiNet Vehicle Detector")
    parser.add_argument("--camera",  type=int,   default=0,     help="Camera index")
    parser.add_argument("--broker",  type=str,   default="localhost", help="MQTT broker IP")
    parser.add_argument("--mode",    type=str,   default="auto",help="auto|mog2|yolo")
    parser.add_argument("--scale",   type=float, default=3.3,   help="Density scale factor")
    args = parser.parse_args()

    CAMERA_INDEX  = args.camera
    MQTT_BROKER   = args.broker
    VEHICLE_SCALE = args.scale

    if args.mode == "yolo":
        load_yolo()
        state["detection_mode"] = "YOLO" if USE_YOLO else "MOG2"
    elif args.mode == "mog2":
        state["detection_mode"] = "MOG2"
    else:  # auto
        if USE_YOLO:
            load_yolo()
        state["detection_mode"] = "YOLO" if USE_YOLO else "MOG2"

    print("=" * 55)
    print("  LumiNet AI — Vehicle Detector")
    print(f"  Camera: {CAMERA_INDEX}  |  Broker: {MQTT_BROKER}")
    print(f"  Mode:   {state['detection_mode']}")
    print("  Press Q to quit  |  Press M to toggle mode")
    print("=" * 55)

    # Start network threads
    threading.Thread(target=mqtt_connect_loop, daemon=True).start()
    threading.Thread(target=socket_connect_loop, daemon=True).start()
    time.sleep(1.5)   # allow connections

    # Start detection (blocking)
    detection_loop()

    # Cleanup
    mqtt_client.loop_stop()
    if sio.connected: sio.disconnect()

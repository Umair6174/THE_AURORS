# ============================================================
#  LumiNet AI — Backend Server v2.1
#  • USB Serial monitor (ESP32 via USB)
#  • MQTT broker bridge (WiFi path from ESP32)
#  • OpenCV traffic density receiver
#  • Flask + Socket.IO → Streamlit frontend
#  • REST API polling fallback for frontend
#  • AI brightness computation per node
#  • Auto-fault detection & alert generation
# ============================================================

import json
import time
import threading
import serial
import serial.tools.list_ports
from datetime import datetime

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# ── CONFIG ───────────────────────────────────────────────────
MQTT_BROKER       = "localhost"
MQTT_PORT         = 1883
DATA_TOPIC        = "lights/data"
CONTROL_TOPIC     = "lights/control"
TRAFFIC_TOPIC     = "lights/traffic"

SERIAL_BAUD       = 115200
SERIAL_SCAN_SEC   = 10          # rescan for new COM ports every N seconds
FAULT_VOLTAGE_THR = 50.0        # V below this → Fault

# AI brightness model weights
W_AMBIENT  = 0.4
W_TRAFFIC  = 0.3
W_ROAD     = 0.2
W_DISTANCE = 0.1

NODE_STATIC = {
    "LN-100": {"road_priority": 80, "distance_factor": 60},
    "LN-101": {"road_priority": 65, "distance_factor": 75},
    "LN-102": {"road_priority": 50, "distance_factor": 90},
}

# ── SHARED STATE ─────────────────────────────────────────────
hardware_state = {
    "LN-100": {
        "status": "Offline", "brightness": 0.0, "voltage": 0.0,
        "uptime_h": 0, "fault_type": "None",
        "ambient_light": 0, "traffic_density": 0.0,
        "raw_ldr": 0, "last_seen": None, "source": "—",
    },
    "LN-101": {
        "status": "Offline", "brightness": 0.0, "voltage": 0.0,
        "uptime_h": 0, "fault_type": "None",
        "ambient_light": 0, "traffic_density": 0.0,
        "raw_ldr": 0, "last_seen": None, "source": "—",
    },
    "LN-102": {
        "status": "Offline", "brightness": 0.0, "voltage": 0.0,
        "uptime_h": 0, "fault_type": "None",
        "ambient_light": 0, "traffic_density": 0.0,
        "raw_ldr": 0, "last_seen": None, "source": "—",
    },
}

latest_traffic = {"density": 0.0, "count": 0, "source": "none"}
alert_log      = []

# ── FLASK + SOCKET.IO ────────────────────────────────────────
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── HELPERS ──────────────────────────────────────────────────
def compute_brightness(node_id, ambient, traffic):
    st = NODE_STATIC.get(node_id, {"road_priority": 60, "distance_factor": 70})
    inverted_ambient = 100.0 - ambient   # dark outside → high value → more brightness
    b  = (W_AMBIENT  * inverted_ambient +
          W_TRAFFIC  * traffic +
          W_ROAD     * st["road_priority"] +
          W_DISTANCE * st["distance_factor"])
    return round(min(100.0, max(0.0, b)), 1)

def determine_status(voltage, brightness):
    if voltage < FAULT_VOLTAGE_THR:
        return "Fault"
    if brightness < 20:
        return "Low Ambient"
    return "Healthy"

def push_alert(msg, level="critical"):
    alert_log.insert(0, {
        "time":  datetime.now().strftime("%H:%M:%S"),
        "msg":   msg,
        "level": level,
    })
    del alert_log[50:]
    socketio.emit("alert", {"time": alert_log[0]["time"], "msg": msg, "level": level})

def process_sensor_payload(payload: dict, source: str):
    light_id = payload.get("light_id")
    if light_id is None:
        return
    node_id = f"LN-{99 + int(light_id)}"
    if node_id not in hardware_state:
        return

    ambient  = float(payload.get("ambient",   50))
    voltage  = float(payload.get("power",    220))
    uptime_h = int(  payload.get("uptime_h",   0))
    raw_ldr  = int(  payload.get("raw_ldr",    0))

    traffic  = latest_traffic["density"]
    bright   = compute_brightness(node_id, ambient, traffic)
    status   = determine_status(voltage, bright)

    prev_status = hardware_state[node_id]["status"]
    hardware_state[node_id].update({
        "status":          status,
        "brightness":      bright,
        "voltage":         voltage,
        "uptime_h":        uptime_h,
        "fault_type":      "Power Loss" if status == "Fault" else "None",
        "ambient_light":   int(ambient),
        "traffic_density": traffic,
        "raw_ldr":         raw_ldr,
        "last_seen":       datetime.now().isoformat(),
        "source":          source,
    })

    if status == "Fault" and prev_status != "Fault":
        push_alert(f"FAULT detected on {node_id} — voltage {voltage:.0f}V", "critical")

    # Send brightness command back over serial
    if source == "serial":
        for port, ser in serial_handles.items():
            try:
                ser.write(f"BRIGHTNESS:{light_id}:{bright:.1f}\n".encode())
            except Exception:
                pass

    # Send brightness command back over MQTT
    if source == "mqtt":
        cmd = json.dumps({f"light{light_id}": bright})
        mqtt_client.publish(CONTROL_TOPIC, cmd)

    socketio.emit("sensor_data", hardware_state)
    print(f"[Node] {node_id} | amb={ambient:.0f}% bright={bright:.1f}% volt={voltage:.0f}V status={status} src={source}")

# ── MQTT ─────────────────────────────────────────────────────
mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="LumiNet_Backend"
)

def on_mqtt_connect(client, userdata, flags, rc, props):
    if rc == 0:
        print("✅ [MQTT] Connected to Mosquitto broker")
        client.subscribe([(DATA_TOPIC, 0), (TRAFFIC_TOPIC, 0)])
    else:
        print(f"❌ [MQTT] Connection failed rc={rc}")

def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        return

    if msg.topic == DATA_TOPIC:
        process_sensor_payload(payload, source="mqtt")
    elif msg.topic == TRAFFIC_TOPIC:
        latest_traffic["density"] = float(payload.get("traffic_density", 0))
        latest_traffic["count"]   = int(  payload.get("vehicle_count",   0))
        latest_traffic["source"]  = "opencv"
        socketio.emit("traffic_update", latest_traffic)

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

def mqtt_thread():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            print(f"[MQTT] Retry in 5s: {e}")
            time.sleep(5)

# ── USB SERIAL MONITOR ───────────────────────────────────────
serial_handles = {}   # port → serial.Serial

def find_esp32_ports():
    ports = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if any(k in desc for k in ["cp210", "ch340", "ftdi", "uart", "usb serial", "esp"]):
            ports.append(p.device)
    return ports

def serial_reader(port):
    print(f"[Serial] Opening {port} @ {SERIAL_BAUD}")
    try:
        ser = serial.Serial(port, SERIAL_BAUD, timeout=1)
        serial_handles[port] = ser
    except serial.SerialException as e:
        print(f"[Serial] ❌ {port}: {e}")
        return

    while serial_handles.get(port):
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("SERIAL_DATA:"):
                raw = line[len("SERIAL_DATA:"):]
                try:
                    payload = json.loads(raw)
                    process_sensor_payload(payload, source="serial")
                except json.JSONDecodeError:
                    print(f"[Serial] Bad JSON: {raw}")
            else:
                # Print all other ESP32 debug lines to console
                print(f"[ESP32] {line}")
        except serial.SerialException:
            print(f"[Serial] Lost connection: {port}")
            break
        except Exception as ex:
            print(f"[Serial] Error: {ex}")

    try:
        ser.close()
    except Exception:
        pass
    serial_handles.pop(port, None)
    print(f"[Serial] Closed {port}")

def serial_scanner():
    while True:
        ports = find_esp32_ports()
        for port in ports:
            if port not in serial_handles:
                t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
                t.start()
        time.sleep(SERIAL_SCAN_SEC)

# ── WATCHDOG ─────────────────────────────────────────────────
def watchdog():
    while True:
        now = datetime.now()
        for node_id, s in hardware_state.items():
            if s["last_seen"]:
                diff = (now - datetime.fromisoformat(s["last_seen"])).total_seconds()
                if diff > 10 and s["status"] != "Offline":
                    hardware_state[node_id]["status"] = "Offline"
                    push_alert(f"{node_id} went offline (no data for {diff:.0f}s)", "warn")
                    socketio.emit("sensor_data", hardware_state)
        time.sleep(3)

# ── SOCKET.IO EVENTS ─────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    emit("sensor_data",    hardware_state)
    emit("traffic_update", latest_traffic)
    emit("alert_log",      alert_log[:20])
    print(f"[Socket] Client connected")

@socketio.on("manual_brightness")
def handle_manual(data):
    node_id   = data.get("node_id")
    bright    = float(data.get("brightness", 50))
    light_num = int(node_id.split("-")[1]) - 99
    if node_id in hardware_state:
        hardware_state[node_id]["brightness"] = bright
        # Serial
        for port, ser in serial_handles.items():
            try:
                ser.write(f"BRIGHTNESS:{light_num}:{bright:.1f}\n".encode())
            except Exception:
                pass
        # MQTT
        try:
            cmd = json.dumps({f"light{light_num}": bright})
            mqtt_client.publish(CONTROL_TOPIC, cmd)
        except Exception:
            pass
        emit("sensor_data", hardware_state, broadcast=True)
        push_alert(f"Manual override: {node_id} → {bright:.0f}%", "info")

# ── REST ENDPOINTS ────────────────────────────────────────────
@app.route("/api/state")
def api_state():
    return jsonify(hardware_state)

@app.route("/api/traffic")
def api_traffic():
    return jsonify(latest_traffic)

@app.route("/api/alerts")
def api_alerts():
    return jsonify(alert_log[:30])

@app.route("/api/health")
def api_health():
    online = sum(1 for s in hardware_state.values() if s["status"] != "Offline")
    return jsonify({
        "status":          "ok",
        "nodes_online":    online,
        "mqtt_connected":  mqtt_client.is_connected(),
        "serial_ports":    list(serial_handles.keys()),
        "traffic_source":  latest_traffic["source"],
    })

@app.route("/api/manual_brightness", methods=["POST"])
def api_manual_brightness():
    """REST fallback for manual brightness when Socket.IO isn't connected."""
    data      = request.get_json()
    node_id   = data.get("node_id")
    bright    = float(data.get("brightness", 50))
    light_num = int(node_id.split("-")[1]) - 99
    if node_id in hardware_state:
        hardware_state[node_id]["brightness"] = bright
        # Serial
        for port, ser in serial_handles.items():
            try:
                ser.write(f"BRIGHTNESS:{light_num}:{bright:.1f}\n".encode())
            except Exception:
                pass
        # MQTT
        try:
            cmd = json.dumps({f"light{light_num}": bright})
            mqtt_client.publish(CONTROL_TOPIC, cmd)
        except Exception:
            pass
        socketio.emit("sensor_data", hardware_state)
        push_alert(f"Manual override (REST): {node_id} → {bright:.0f}%", "info")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Unknown node_id"}), 400

# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  LumiNet AI — Backend Server v2.1")
    print(f"  MQTT Broker  : {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  REST + WS    : http://0.0.0.0:5000")
    print(f"  Serial scan  : every {SERIAL_SCAN_SEC}s")
    print("=" * 55)

    threading.Thread(target=mqtt_thread,    daemon=True).start()
    threading.Thread(target=serial_scanner, daemon=True).start()
    threading.Thread(target=watchdog,       daemon=True).start()

    time.sleep(1)
    socketio.run(app, host="0.0.0.0", port=5000,
                 debug=False, allow_unsafe_werkzeug=True)

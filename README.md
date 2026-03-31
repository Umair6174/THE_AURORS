# LumiNet AI — Full System Setup Guide

## System Architecture

```
[ESP32 Node 1 (LN-100)]  ─┐
[ESP32 Node 2 (LN-101)]  ─┤── WiFi / MQTT ──► [Mosquitto Broker]
[ESP32 Node 3 (LN-102)]  ─┘                         │
                                                      ▼
[Laptop Camera] ── OpenCV ──► MQTT (lights/traffic) ─┤
                                                      │
                                               [backend.py]
                                               Flask + Socket.IO
                                                      │
                                               [app.py]
                                               Streamlit Frontend
```

## File Overview

| File | Purpose |
|---|---|
| `luminet_node.ino` | Flash to each ESP32 (change NODE_NUMBER per device) |
| `backend.py` | Central server: MQTT bridge + serial monitor + AI |
| `vehicle_detector.py` | OpenCV traffic detection from webcam |
| `app.py` | Streamlit dashboard |
| `requirements.txt` | Python dependencies |

---

## Step 1 — Hardware Wiring (per ESP32)

```
ESP32 GPIO34  ────[LDR]────[10kΩ to GND]────  3.3V
                    └── ADC reads voltage divider

ESP32 GPIO2   ────[220Ω]────[LED+]────[LED-]──── GND
               (or via NPN transistor for brighter LED)
```

**Better LED circuit (for visible brightness control):**
```
GPIO2 ──[220Ω]──► NPN Base (BC547)
                  Collector ──► LED+ ──► LED- ──► GND
                  Emitter ──► GND
                  (Power LED from 5V through transistor)
```

---

## Step 2 — Flash ESP32 Firmware

1. Open `luminet_node.ino` in Arduino IDE
2. Install libraries:
   - `PubSubClient` by Nick O'Leary
   - `ArduinoJson` by Benoit Blanchon
3. Edit the top config section:
```cpp
#define NODE_NUMBER     1          // 1, 2, or 3
#define WIFI_SSID       "YourSSID"
#define WIFI_PASSWORD   "YourPassword"
#define MQTT_BROKER     "192.168.1.XXX"  // Your laptop IP
```
4. Select Board: `ESP32 Dev Module`
5. Flash — repeat for nodes 2 and 3 changing NODE_NUMBER

---

## Step 3 — Install Mosquitto MQTT Broker

**Windows:**
```
winget install mosquitto
# Start broker:
mosquitto -v
```

**Linux/Mac:**
```bash
sudo apt install mosquitto mosquitto-clients   # Linux
brew install mosquitto                          # Mac
mosquitto -v
```

---

## Step 4 — Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 5 — Find Your Laptop IP

```bash
# Windows
ipconfig
# Look for: IPv4 Address . . . . . . . : 192.168.x.x

# Linux/Mac
ifconfig | grep "inet "
```

Set this IP as `MQTT_BROKER` in the ESP32 firmware.

---

## Step 6 — Run the System (3 terminals)

**Terminal 1 — Backend:**
```bash
python backend.py
```

**Terminal 2 — Vehicle Detector:**
```bash
python vehicle_detector.py --camera 0 --broker localhost
# Options:
#   --camera 1     (if 0 is wrong camera)
#   --mode mog2    (force background subtraction)
#   --mode yolo    (force YOLO — needs weights file)
#   --scale 4.0    (tune density sensitivity)
```

**Terminal 3 — Dashboard:**
```bash
streamlit run app.py
```

Open browser: `http://localhost:8501`

---

## MQTT Topics Reference

| Topic | Direction | Payload |
|---|---|---|
| `lights/data` | ESP32 → Backend | `{"light_id":1,"ambient":45,"power":220,"uptime_h":5,"raw_ldr":1800}` |
| `lights/control` | Backend → ESP32 | `{"light1":72.5}` |
| `lights/traffic` | OpenCV → Backend | `{"traffic_density":35.0,"vehicle_count":3}` |

---

## Login Credentials

| Role | Username | Password |
|---|---|---|
| Admin | admin | admin123 |
| Technician | tech | tech123 |
| Viewer | viewer | view123 |

---

## Node Assignment

| Node ID | ESP32 # | NODE_NUMBER |
|---|---|---|
| LN-100 | Node 1 | 1 |
| LN-101 | Node 2 | 2 |
| LN-102 | Node 3 | 3 |

---

## USB Serial Fallback

If WiFi/MQTT fails, the ESP32 still prints:
```
SERIAL_DATA:{"light_id":1,"ambient":45,"power":220,...}
```
The backend auto-detects ESP32 USB ports (CH340/CP210x chips)
and reads this data automatically — no config needed.

You can also send manual brightness commands via Serial Monitor:
```
BRIGHTNESS:1:75.0
```

---

## OpenCV Tuning Tips

- **Too many false detections?** Increase `MIN_CONTOUR_AREA` (default 800)
- **Missing vehicles?** Decrease `MIN_CONTOUR_AREA` or `varThreshold` in MOG2
- **Density too high/low?** Adjust `--scale` argument (default 3.3)
- **Press M** while the detector window is open to toggle MOG2 ↔ YOLO

---

## Optional: YOLO Setup

1. Download YOLOv4-tiny weights:
```bash
wget https://github.com/AlexeyAB/darknet/releases/download/darknet_yolo_v4_pre/yolov4-tiny.weights
wget https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg
```
2. Place both files in the same folder as `vehicle_detector.py`
3. Run: `python vehicle_detector.py --mode yolo`

---

## Troubleshooting

| Issue | Fix |
|---|---|
| ESP32 not connecting to MQTT | Check laptop IP in firmware, ensure Mosquitto is running |
| No serial data | Check COM port drivers (CH340 / CP210x), try different USB cable |
| Camera not opening | Try `--camera 1` or `--camera 2` |
| Frontend shows "WAITING FOR DATA" | Start backend.py first, then streamlit |
| Socket disconnected | Backend not running or port 5000 blocked |

// ============================================================
//  LumiNet AI — ESP32 Serial Node v1.0
//  No WiFi — communicates via USB Serial to backend.py
//  
//  Sends:    SERIAL_DATA:{"light_id":1,"ambient":72,...}
//  Receives: BRIGHTNESS:1:85.0
//
//  Pins: LDR: 34, 35, 32  |  LED: 25, 26, 27
// ============================================================

#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_task_wdt.h"

// ── PINS ────────────────────────────────────────────────────
const int LDR_PINS[3] = {34, 35, 32};
const int LED_PINS[3] = {25, 26, 27};

#define PWM_FREQ  1000
#define PWM_RES   8

// ── GLOBALS ─────────────────────────────────────────────────
float aiBrightness[3]    = {50.0, 50.0, 50.0};
unsigned long lastSendMs = 0;
#define SEND_INTERVAL_MS  2000

// ── LED ──────────────────────────────────────────────────────
void setLED(int index, float pct) {
  pct = constrain(pct, 0.0f, 100.0f);
  int duty = (int)map((long)pct, 0, 100, 30, 255);
  ledcWrite(LED_PINS[index], duty);
}

// ── SEND SENSOR DATA ─────────────────────────────────────────
// Backend expects: SERIAL_DATA:{"light_id":1,"ambient":72,"power":220,"uptime_h":0,"raw_ldr":2048}
void sendSensorData() {
  unsigned long uptimeH = millis() / 3600000UL;

  for (int i = 0; i < 3; i++) {
    int rawLDR     = analogRead(LDR_PINS[i]);
    int ambientPct = (int)map(rawLDR, 0, 4095, 0, 100);

    Serial.printf("SERIAL_DATA:{\"light_id\":%d,\"ambient\":%d,\"power\":220,\"uptime_h\":%lu,\"raw_ldr\":%d}\n",
                  i + 1, ambientPct, uptimeH, rawLDR);

    delay(20);
  }
}

// ── READ BRIGHTNESS COMMANDS FROM BACKEND ────────────────────
// Backend sends: BRIGHTNESS:1:85.0
void readCommands() {
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("BRIGHTNESS:")) {
      // Parse BRIGHTNESS:<light_num>:<value>
      int firstColon  = line.indexOf(':', 11);
      if (firstColon == -1) return;

      int lightNum = line.substring(11, firstColon).toInt();         // 1, 2, 3
      float bright = line.substring(firstColon + 1).toFloat();       // 0–100

      if (lightNum >= 1 && lightNum <= 3) {
        aiBrightness[lightNum - 1] = bright;
        setLED(lightNum - 1, bright);
        // Confirm back to backend (optional debug)
        Serial.printf("[CMD] light%d → %.1f%%\n", lightNum, bright);
      }
    }
  }
}

// ── SETUP ────────────────────────────────────────────────────
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  esp_task_wdt_deinit();

  Serial.begin(115200);
  delay(1000);

  Serial.println("========================================");
  Serial.println("  LumiNet AI — ESP32 Serial Node v1.0");
  Serial.println("  No WiFi — USB Serial mode");
  Serial.println("========================================");

  // PWM init
  for (int i = 0; i < 3; i++) {
    ledcAttach(LED_PINS[i], PWM_FREQ, PWM_RES);
  }

  // Default LEDs to 50%
  for (int i = 0; i < 3; i++) setLED(i, 50.0);

  Serial.println("[PWM] LEDs initialised at 50%");
  Serial.println("[System] Running — sending data every 2s");
  Serial.println("========================================\n");
}

// ── LOOP ─────────────────────────────────────────────────────
void loop() {
  // Always check for incoming brightness commands
  readCommands();

  // Send sensor data every 2s
  unsigned long now = millis();
  if (now - lastSendMs >= SEND_INTERVAL_MS) {
    lastSendMs = now;
    sendSensorData();
  }

  delay(10);
}
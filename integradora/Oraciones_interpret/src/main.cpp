#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>

/*
 * Firmware "periférico puro" para el ESP32 de Moodi/BMO.
 *
 * Responsabilidad ÚNICA de este firmware: leer hardware (10 botones + lector
 * RFID RC522) y reportarlo por serie en texto plano. NO hace WiFi, NO llama a
 * ia_bridge, NO envía Telegram y NO traduce UID->palabra: toda esa lógica
 * (orquestación, vocabulario, LLM, Telegram) vive ahora en bmo_app.py, en la
 * Jetson, para tener una sola fuente de verdad del estado de la frase PECS y
 * para no bloquear la lectura de botones/RFID si la red cae (que es lo que
 * pasaba con el WiFi.begin() bloqueante de la versión anterior).
 *
 * Protocolo de línea (una línea por evento, terminada en '\n'):
 *   BTN:<gpio>:DOWN      -> botón en <gpio> presionado
 *   BTN:<gpio>:UP        -> botón en <gpio> liberado
 *   RFID:<b0>,<b1>,<b2>,<b3>  -> UID crudo de una tarjeta leída
 *   BOOT:OK              -> saludo al iniciar/reconectar
 *
 * El mapeo GPIO -> rol lógico (D-pad, cluster de acción, etc.) vive
 * exclusivamente en bmo_unified/config/button_map.json (Python), nunca aquí,
 * para poder remapear sin reflashear la placa.
 */

// ---------- RFID ----------
constexpr uint8_t SS_PIN  = 21;
constexpr uint8_t RST_PIN = 22;
MFRC522 rfid(SS_PIN, RST_PIN);

// ---------- Botones ----------
// GPIO según la tabla 3.2 de CONTEXTO_MOODI_UI_INTERACTIVA_08072026.md (mapa definitivo,
// módulo ESP32-WROOM). Orden: CURSOR_UP=4, CURSOR_DOWN=16, CURSOR_LEFT=32, CURSOR_RIGHT=33,
// ISLA_R=25, ISLA_M=26, ISLA_L=27, PANEL_R=17, PANEL_L=14, PANEL_C=13. GPIO34/35 (solo
// entrada, sin pull-up interna) y GPIO12 (strapping de boot) quedan fuera de uso -- los 10
// pines finales soportan INPUT_PULLUP interno, mismo cableado simple (botón a GND,
// presionado = LOW), sin resistencias externas. Ver bmo_unified/config/button_map.json
// para el mapeo GPIO -> rol lógico (única fuente de verdad, no aquí).
constexpr uint8_t NUM_BUTTONS = 10;
constexpr uint8_t BUTTON_PINS[NUM_BUTTONS] = {4, 16, 32, 33, 25, 26, 27, 17, 14, 13};
constexpr uint16_t DEBOUNCE_MS = 50;

bool stableState[NUM_BUTTONS];      // true = liberado (HIGH), false = presionado (LOW)
bool lastReading[NUM_BUTTONS];
unsigned long lastChangeMs[NUM_BUTTONS];

void setupButtons() {
  for (uint8_t i = 0; i < NUM_BUTTONS; ++i) {
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    stableState[i] = HIGH;
    lastReading[i] = HIGH;
    lastChangeMs[i] = 0;
  }
}

void pollButtons() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < NUM_BUTTONS; ++i) {
    bool reading = digitalRead(BUTTON_PINS[i]);

    if (reading != lastReading[i]) {
      lastChangeMs[i] = now;
      lastReading[i] = reading;
    }

    if ((now - lastChangeMs[i]) > DEBOUNCE_MS && reading != stableState[i]) {
      stableState[i] = reading;
      bool pressed = (reading == LOW);
      Serial.print("BTN:");
      Serial.print(BUTTON_PINS[i]);
      Serial.println(pressed ? ":DOWN" : ":UP");
    }
  }
}

// ---------- RFID ----------
void readRFID() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  Serial.print("RFID:");
  for (uint8_t i = 0; i < 4; ++i) {
    Serial.print(rfid.uid.uidByte[i]);
    if (i < 3) Serial.print(",");
  }
  Serial.println();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

void setup() {
  Serial.begin(115200);
  SPI.begin();
  rfid.PCD_Init();
  setupButtons();
  Serial.println("BOOT:OK");
}

void loop() {
  pollButtons();
  readRFID();
}

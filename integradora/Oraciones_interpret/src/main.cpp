#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>
#include <FS.h>
#include <SPIFFS.h>
#include <ArduinoJson.h>
#include <vector>
#include <WiFi.h>
#include <HTTPClient.h>

/* CONFIGURACIÓN */
constexpr char  SSID[]       = "NETLIFE-FERNANDO";
constexpr char  PASS[]       = "Mayala2002";
constexpr char  SERVER_URL[] = "http://192.168.10.140:5000/ask";

constexpr char TELEGRAM_TOKEN[] = "8019998276:AAEmcn7N2ZRH_hp9VfEVqrW5WX4vDSg8xT4";
constexpr char CHAT_ID[]        = "1344148877";

constexpr uint32_t HTTP_TIMEOUT = 90000;   // 90s para cubrir picos
constexpr uint8_t  SS_PIN       = 21;
constexpr uint8_t  RST_PIN      = 22;
constexpr uint8_t  BUTTON_PIN   = 5;
constexpr uint16_t DEBOUNCE_MS  = 50;

// RFID
MFRC522 rfid(SS_PIN, RST_PIN);
byte card[4] = {0};
String sentence;

// botón
bool buttonState = HIGH, lastButton = HIGH;
unsigned long lastDebounce = 0;
bool readyToSend = false;

// vocabulario
struct RFIDWord {
  byte uid[4];
  String palabra;
  String tipo;
};
std::vector<RFIDWord> vocab;

bool sameUID(const byte a[4], const byte b[4]) {
  for (int i = 0; i < 4; ++i) if (a[i] != b[i]) return false;
  return true;
}

RFIDWord* findWord(const byte uid[4]) {
  for (auto& w : vocab) if (sameUID(w.uid, uid)) return &w;
  return nullptr;
}

void loadVocab() {
  if (!SPIFFS.begin(true)) { Serial.println("[ERR] SPIFFS"); return; }
  File f = SPIFFS.open("/rfid_vocab.json");
  if (!f) { Serial.println("[ERR] vocab file"); return; }

  DynamicJsonDocument doc(8192);
  if (deserializeJson(doc, f)) { Serial.println("[ERR] JSON"); return; }

  for (JsonObject o : doc.as<JsonArray>()) {
    RFIDWord w;
    for (int i = 0; i < 4; ++i) w.uid[i] = o["uid"][i];
    w.palabra = o["palabra"].as<String>();
    w.tipo    = o["tipo"].as<String>();
    vocab.push_back(w);
  }
  Serial.println("[OK] Vocabulario cargado");
}

void printUID(const byte *b) {
  for (int i = 0; i < 4; ++i) { Serial.print(b[i]); if (i < 3) Serial.print(","); }
}

// WiFi
void connectWiFi() {
  WiFi.setSleep(false);           // evita microcortes
  WiFi.mode(WIFI_STA);
  WiFi.begin(SSID, PASS);
  Serial.print("Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print('.'); }
  Serial.println(" ¡conectado!");
}

// Telegram
void sendTelegramMessage(const String& mensaje) {
  String url = "https://api.telegram.org/bot" + String(TELEGRAM_TOKEN) + "/sendMessage";

  HTTPClient http;
  WiFiClientSecure tls;
  tls.setInsecure(); 

  http.setReuse(false);
  http.useHTTP10(true);       // cerrar al final
  http.begin(tls, url);
  http.setTimeout(HTTP_TIMEOUT);
  #if ARDUINO_ESP32_MAJOR >= 3
    http.setConnectTimeout(15000);
  #endif

  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "close");

  String payload = "{\"chat_id\":\"" + String(CHAT_ID) + "\",\"text\":\"" + mensaje + "\"}";

  int code = http.POST(payload);
  if (code > 0) Serial.println("[OK] Mensaje enviado a Telegram");
  else         Serial.printf("[ERROR] Telegram HTTP %d\n", code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  SPI.begin();
  rfid.PCD_Init();
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  connectWiFi();
  loadVocab();
  Serial.println("Escanea una tarjeta...");
}

// lectura RFID
void readRFID() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  memcpy(card, rfid.uid.uidByte, 4);
  Serial.print("UID: "); printUID(card); Serial.print(" → ");

  if (RFIDWord* w = findWord(card)) {
    Serial.println(w->palabra);
    sentence += w->palabra + " ";
  } else {
    Serial.println("¿?");
  }
  rfid.PICC_HaltA(); rfid.PCD_StopCrypto1();
}

// botón
void pollButton() {
  bool reading = digitalRead(BUTTON_PIN);
  if (reading != lastButton) lastDebounce = millis();

  if ((millis() - lastDebounce) > DEBOUNCE_MS && reading != buttonState) {
    buttonState = reading;
    if (buttonState == LOW && sentence.length()) readyToSend = true;
  }
  lastButton = reading;
}

// enviar a IA
void sendToAI() {
  String compact = sentence;
  compact.trim();
  compact.replace("  ", " ");

  Serial.println("\n===== FRASE BRUTA =====");
  Serial.println(sentence);
  Serial.println("=======================");
  Serial.print("→ Compactada: "); Serial.println(compact);
  Serial.println();

  String prompt = "Reescribe la frase en español correcto y conciso: \"" + compact + "\"";
  prompt.replace("\"", "\\\"");

  // payload JSON
  DynamicJsonDocument jd(1024);
  jd["prompt"] = prompt;
  String payload; serializeJson(jd, payload);

  auto do_request = [&](String &response) -> int {
    HTTPClient http;
    WiFiClient client;                 // cliente explícito
    client.setTimeout(HTTP_TIMEOUT);

    http.setReuse(false);              // sin keep-alive
    http.useHTTP10(true);              // cierre al final
    http.begin(client, SERVER_URL);
    http.setTimeout(HTTP_TIMEOUT);
    #if ARDUINO_ESP32_MAJOR >= 3
      http.setConnectTimeout(15000);
    #endif
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Accept", "application/json");
    http.addHeader("Connection", "close");

    unsigned long t0 = millis();
    Serial.print("⌛ Esperando IA");
    int code = http.POST(payload);

    if (code == HTTP_CODE_OK) {
      response = http.getString();     // bloquea hasta recibir todo o timeout
    } else {
      Serial.printf(" [HTTP %d]\n", code);
    }
    http.end();

    Serial.println();
    unsigned long total = millis() - t0;
    Serial.printf("[TIEMPO TOTAL] %lu ms\n", total);
    return code;
  };

  // intento 1
  String response;
  int code = do_request(response);

  // reintento si timeout transitorio (-11) o error de transporte (-1)
  if (code == -11 || code == -1) {
    delay(400);
    Serial.println("[INFO] Reintentando petición a IA...");
    response = "";
    code = do_request(response);
  }

  DynamicJsonDocument jr(2048);
  if (code == HTTP_CODE_OK &&
      deserializeJson(jr, response) == DeserializationError::Ok &&
      jr.containsKey("response")) {

    String msg = jr["response"].as<String>();
    msg.replace("**", "");
    msg.trim();
    if (msg.startsWith("\"") && msg.endsWith("\"")) {
      msg.remove(0, 1); msg.remove(msg.length() - 1);
    }

    Serial.println("Interpretación IA:");
    Serial.println(msg);

    String mensajeTelegram = "El niño comunica el siguiente mensaje 📨:\n\n" + msg;
    sendTelegramMessage(mensajeTelegram);
  } else {
    Serial.println("Error JSON / Respuesta:");
    Serial.println(response);
  }

  sentence = "";
  readyToSend = false;
  Serial.println("\nEscanea una tarjeta...");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  readRFID();
  pollButton();
  if (readyToSend) sendToAI();
}

// Firmware JARVIS — punto de voz tipo Alexa (LilyGo T-A7670G / ESP32).
//
// Esqueleto: conecta a la red (WiFi) y al broker MQTT del servidor JARVIS_OS, envía texto
// a JARVIS y recibe respuestas. La captura de audio (I2S), la pantalla y el 4G se añaden
// encima de esta base (ver docs/roadmap.md, Fase 4).
//
// Topics:
//   jarvis/device/<id>/in      -> lo que este punto envía a JARVIS
//   jarvis/device/<id>/out     <- respuesta dirigida a este punto
//   jarvis/device/<id>/status  -> heartbeat
//   jarvis/broadcast           <- avisos proactivos para todos los puntos de la casa

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "config.h"

WiFiClient netClient;
PubSubClient mqtt(netClient);

static char topicIn[64];
static char topicOut[64];
static char topicStatus[64];

unsigned long lastHeartbeat = 0;

void buildTopics() {
  snprintf(topicIn, sizeof(topicIn), "jarvis/device/%s/in", JARVIS_DEVICE_ID);
  snprintf(topicOut, sizeof(topicOut), "jarvis/device/%s/out", JARVIS_DEVICE_ID);
  snprintf(topicStatus, sizeof(topicStatus), "jarvis/device/%s/status", JARVIS_DEVICE_ID);
}

// Llega una respuesta de JARVIS (o un aviso proactivo).
void onMessage(char* topic, byte* payload, unsigned int length) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[MQTT] payload no-JSON en %s\n", topic);
    return;
  }
  const char* text = doc["reply"] | doc["text"] | "";
  Serial.printf("[JARVIS] %s\n", text);
  // TODO: reproducir por TTS/altavoz y/o mostrar en la pantalla.
}

void connectWiFi() {
  Serial.printf("[WiFi] Conectando a %s", JARVIS_WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(JARVIS_WIFI_SSID, JARVIS_WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Conectado: %s\n", WiFi.localIP().toString().c_str());
}

void connectMQTT() {
  mqtt.setServer(JARVIS_MQTT_HOST, JARVIS_MQTT_PORT);
  mqtt.setCallback(onMessage);
  while (!mqtt.connected()) {
    Serial.print("[MQTT] Conectando...");
    bool ok;
    if (strlen(JARVIS_MQTT_USER) > 0) {
      ok = mqtt.connect(JARVIS_DEVICE_ID, JARVIS_MQTT_USER, JARVIS_MQTT_PASSWORD);
    } else {
      ok = mqtt.connect(JARVIS_DEVICE_ID);
    }
    if (ok) {
      Serial.println(" ok");
      mqtt.subscribe(topicOut);
      mqtt.subscribe("jarvis/broadcast");
    } else {
      Serial.printf(" fallo rc=%d, reintento en 2s\n", mqtt.state());
      delay(2000);
    }
  }
}

// Envía texto a JARVIS. En un dispositivo real, este texto vendría del STT tras oír al
// usuario (o de un botón). Aquí se usa como demostración.
void sendToJarvis(const char* text) {
  JsonDocument doc;
  doc["text"] = text;
  doc["device"] = JARVIS_DEVICE_ID;
  char buffer[256];
  size_t n = serializeJson(doc, buffer);
  mqtt.publish(topicIn, buffer, n);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  buildTopics();
  connectWiFi();
  connectMQTT();

  // Demo: saludar al servidor al arrancar.
  sendToJarvis("Hola JARVIS, el punto de voz del salón está en línea.");
}

void loop() {
  if (!mqtt.connected()) {
    connectMQTT();
  }
  mqtt.loop();

  // Heartbeat cada 30 s.
  unsigned long now = millis();
  if (now - lastHeartbeat > 30000) {
    lastHeartbeat = now;
    mqtt.publish(topicStatus, "{\"status\":\"online\"}");
  }

  // TODO (Fase 4): leer micrófono I2S -> detectar palabra clave -> enviar audio/STT.
}

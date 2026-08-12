// Configuración del dispositivo JARVIS.
// Copia este fichero a `config.h` y rellena tus valores. NO subas config.h al repo.

#pragma once

// Identificador único de este punto de voz (uno por dispositivo de la casa).
#define JARVIS_DEVICE_ID      "salon"

// --- Red: WiFi (por defecto) ---
#define JARVIS_WIFI_SSID      "tu-wifi"
#define JARVIS_WIFI_PASSWORD  "tu-password"

// --- Broker MQTT (tu servidor JARVIS_OS) ---
#define JARVIS_MQTT_HOST      "192.168.1.100"   // IP del servidor en tu red
#define JARVIS_MQTT_PORT      1883
#define JARVIS_MQTT_USER      ""                // vacío si el broker es anónimo (solo LAN)
#define JARVIS_MQTT_PASSWORD  ""

// --- 4G LTE (opcional, para dispositivos fuera de casa) ---
// #define JARVIS_USE_LTE
#define JARVIS_APN            "internet"        // APN de tu operador
#define JARVIS_APN_USER       ""
#define JARVIS_APN_PASSWORD   ""

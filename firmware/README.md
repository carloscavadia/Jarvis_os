# Firmware JARVIS — dispositivos ESP32 (LilyGo T-A7670G)

Firmware para los dispositivos físicos de JARVIS: una placa **LilyGo T-A7670G R2** (ESP32 +
módem 4G LTE Cat-1) que habla con el gateway por **MQTT**. Sirve como control remoto por voz
o texto con pantalla, incluso fuera de tu red local gracias al 4G.

## Qué hace este esqueleto

1. Conecta a la red (WiFi por defecto; 4G opcional vía módem A7670G).
2. Se conecta al broker MQTT del gateway.
3. Publica en `jarvis/device/<id>/in` lo que quieras enviar a JARVIS.
4. Escucha `jarvis/device/<id>/out` y muestra/reproduce la respuesta.
5. Envía un heartbeat en `jarvis/device/<id>/status`.

Es un **punto de partida**: la captura de audio (I²S), la pantalla y el 4G se añaden en la
Fase 4 de la hoja de ruta.

## Requisitos

- [PlatformIO](https://platformio.org/) (extensión de VS Code o CLI).
- La placa LilyGo T-A7670G R2 con su antena LTE (y SIM de datos si usas 4G).

## Configuración

```bash
cp src/config.example.h src/config.h
$EDITOR src/config.h        # WiFi/APN, broker MQTT, device_id
```

## Compilar y flashear

```bash
pio run                      # compilar
pio run --target upload      # flashear por USB
pio device monitor           # ver el log serie
```

## Convención de topics

| Topic                              | Dirección | Uso                          |
|------------------------------------|-----------|------------------------------|
| `jarvis/device/<id>/in`            | → JARVIS  | texto/comando del dispositivo|
| `jarvis/device/<id>/out`           | ← JARVIS  | respuesta del agente         |
| `jarvis/device/<id>/status`        | ↔         | heartbeat / estado           |

## WiFi vs 4G

- **WiFi** (por defecto en este esqueleto): simple, para dispositivos en casa.
- **4G LTE** (A7670G): usa la librería **TinyGSM** + un cliente MQTT sobre el `TinyGsmClient`.
  Descomenta la sección 4G en `main.cpp` y pon el APN de tu operador en `config.h`.
  Recuerda: el módem 4G tiene picos de consumo — usa alimentación capaz de dar >2 A.

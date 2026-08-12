# Firmware JARVIS — dispositivos ESP32 (LilyGo T-A7670G)

Firmware para los dispositivos físicos de JARVIS: una placa **LilyGo T-A7670G R2** (ESP32 +
módem 4G LTE Cat-1) que habla con el gateway por **MQTT**. Es un **punto de voz tipo Alexa**
con **pantalla que muestra la animación reactiva de JARVIS** (el orbe de la película), y
funciona incluso fuera de tu red local gracias al 4G.

> **Dos proyectos, un contrato.** El servidor (`core/` + `gateway/`) y el dispositivo son
> proyectos independientes que se comunican por el protocolo de [`docs/protocol.md`](../docs/protocol.md).
> Puedes desarrollar el firmware sin tocar el servidor y viceversa.

## Qué hace este esqueleto

1. Conecta a la red (WiFi por defecto; 4G opcional vía módem A7670G).
2. Se conecta al broker MQTT del gateway.
3. Publica en `jarvis/device/<id>/in` lo que quieras enviar a JARVIS.
4. Escucha `jarvis/device/<id>/out` (respuesta) y `jarvis/device/<id>/state` (estado).
5. Envía un heartbeat en `jarvis/device/<id>/status`.

Es un **punto de partida**: la captura de audio (I²S), la **pantalla con la animación** y el
4G se añaden en la Fase 4 de la hoja de ruta.

## La animación reactiva (HUD)

El servidor emite el estado de JARVIS (`idle` / `listening` / `thinking` / `speaking`) por
`jarvis/device/<id>/state`. El dispositivo usa ese estado para animar su pantalla, igual que
el JARVIS de Iron Man reacciona al escuchar, procesar y responder.

**Referencia visual lista:** `clients/web-hud/` implementa exactamente esa animación en el
navegador. Ábrela, pulsa los botones de estado y verás cómo debe comportarse la pantalla del
ESP32 en cada fase — es el modelo a portar al display.

Para el display físico, dos caminos habituales en ESP32:
- **TFT_eSPI** — dibujar el orbe/anillos a mano (control total, más trabajo).
- **LVGL** — animaciones y widgets de más alto nivel.

El firmware mantiene una pequeña máquina de estados que, al recibir un `state`, cambia la
animación mostrada. La lógica de red de este esqueleto ya deja el "gancho" donde conectar
ese render.

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

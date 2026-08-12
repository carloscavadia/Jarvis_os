# Protocolo JARVIS_OS — contrato entre servidor y dispositivos

JARVIS_OS son, en la práctica, **dos proyectos** que se hablan por un contrato común:

1. **El servidor** (`core/` + `gateway/`) — el cerebro y la puerta de red.
2. **El dispositivo / HUD** (`firmware/` para ESP32, `clients/web-hud/` en web) — con quien
   te comunicas y que muestra la **animación reactiva de JARVIS** (el orbe de la película).

Este documento define el contrato para que ambos evolucionen por separado.

## Estados de JARVIS (lo que anima el HUD)

El servidor comunica en qué estado está para que el dispositivo reaccione visualmente, igual
que el JARVIS de Iron Man reacciona cuando escucha, procesa y responde:

| Estado       | Cuándo                                   | Animación sugerida                    |
|--------------|------------------------------------------|---------------------------------------|
| `idle`       | En reposo, esperando                     | Respiración lenta del núcleo, cian    |
| `listening`  | Recibiendo/oyendo al usuario             | Ondas expansivas, brillo mayor        |
| `thinking`   | El agente está razonando/usando tools    | Giro rápido, barrido de escaneo, violeta |
| `speaking`   | Entregando la respuesta (TTS/pantalla)   | Núcleo modulado por la voz, ámbar     |

## Transporte

### Autenticación

El servicio principal es la autoridad. Los HUB no contienen credenciales del LLM ni
herramientas: se autentican contra el gateway con `JARVIS_GATEWAY_API_KEY`.

- REST: cabecera `X-Jarvis-Key: <clave>`.
- WebSocket/HUB web: parámetro `?token=<clave>`.
- MQTT: permanece desactivado hasta configurar credenciales y TLS para dispositivos.

### WebSocket (apps / web / HUD)

Conexión: `ws://<servidor>:8080/ws/<session_id>?token=<JARVIS_GATEWAY_API_KEY>`

El gateway rechaza conexiones sin una clave válida. En producción remota usa `wss://`
detrás de un proxy TLS; evita registrar la URL completa porque contiene el token.

El cliente envía **texto plano** (el mensaje del usuario). El servidor responde con
**mensajes JSON**, uno por evento:

```json
{ "type": "state",     "state": "thinking" }
{ "type": "emotion",   "emotion": "focused" }
{ "type": "event",     "event": "execution", "label": "system_info" }
{ "type": "reply",     "reply": "Todo en orden, jefe.", "tools_used": ["system_info"] }
{ "type": "proactive", "source": "task", "text": "Recordatorio: reunión en 10 min." }
```

- **`state`** — mueve el comportamiento del enjambre (energía, turbulencia, expansión).
- **`emotion`** — mueve el **color** del enjambre (ver tabla de emociones abajo).
- **`event`** — dispara un *flare* puntual; se envía uno por cada herramienta ejecutada.
- **`reply`** / **`proactive`** — texto a mostrar/reproducir.

### Emociones (color del enjambre)

| Emoción    | Color        | Uso                                   |
|------------|--------------|---------------------------------------|
| `neutral`  | cian-verdoso | por defecto                           |
| `happy`    | verde-cian   | confirmaciones, buenas noticias       |
| `alert`    | cian brillante | atención, algo requiere acción      |
| `focused`  | violeta      | razonando / trabajando duro           |
| `concern`  | ámbar        | duda, advertencia, error              |

### MQTT (dispositivos ESP32)

| Topic                              | Dirección | Payload                                  |
|------------------------------------|-----------|------------------------------------------|
| `jarvis/device/<id>/in`            | → JARVIS  | `{"text": "..."}` o texto plano          |
| `jarvis/device/<id>/out`           | ← JARVIS  | `{"reply": "..."}`                       |
| `jarvis/device/<id>/state`         | ← JARVIS  | `{"state": "thinking"}`                  |
| `jarvis/device/<id>/status`        | → JARVIS  | `{"status": "online"}` (heartbeat)       |
| `jarvis/broadcast`                 | ← JARVIS  | `{"type":"proactive","text":"..."}`      |

El dispositivo se suscribe a `.../out`, `.../state` y `jarvis/broadcast`, y publica en
`.../in` y `.../status`. Los estados de `.../state` son los que mueven la animación.

## Flujo de una interacción por voz (objetivo Fase 4)

```
Usuario habla
   │  (micro I2S en el ESP32)
   ▼
Dispositivo → jarvis/device/<id>/in   (audio o texto de STT local)
   │
   ▼
Servidor: state=listening → state=thinking → (agente) → state=speaking
   │
   ├─ jarvis/device/<id>/state  (el HUD anima cada fase)
   └─ jarvis/device/<id>/out    (texto/audio de la respuesta)
   │
   ▼
Dispositivo reproduce por TTS/altavoz y muestra el orbe "hablando"
```

## Referencia visual

`clients/web-hud/` implementa este protocolo en el navegador y es la **referencia de la
animación** (los cuatro estados) para portarla al display del ESP32. Ábrelo, pulsa los
botones de estado y verás exactamente cómo debe reaccionar cada dispositivo físico.

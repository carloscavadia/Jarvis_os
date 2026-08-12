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
{ "type": "reply_start" }
{ "type": "reply_delta", "delta": "Todo " }
{ "type": "reply_delta", "delta": "en orden" }
{ "type": "emotion",   "emotion": "focused" }
{ "type": "event",     "event": "execution", "label": "system_info" }
{ "type": "reply",     "reply": "Todo en orden, jefe.", "tools_used": ["system_info"] }
{ "type": "proactive", "source": "task", "text": "Recordatorio: reunión en 10 min." }
{ "type": "approval_required", "approval_id": "...", "tool": "install_package", "summary": "..." }
{ "type": "approval_resolved", "approval_id": "...", "approved": true, "reason": "user" }
{ "type": "tool_event", "phase": "running", "tool": "run_python_file", "arguments": {"path":"informe.py"}, "code":"..." }
```

- **`state`** — mueve el comportamiento del enjambre (energía, turbulencia, expansión).
- **`reply_start`** — abre una respuesta progresiva y vacía en el HUD.
- **`reply_delta`** — añade texto visible conforme llega del proveedor. Los clientes deben
  concatenar `delta` en orden y no asumir que coincide con palabras completas.
- **`emotion`** — mueve el **color** del enjambre (ver tabla de emociones abajo).
- **`event`** — dispara un *flare* puntual; se envía uno por cada herramienta ejecutada.
- **`reply`** — cierra el stream y contiene el texto completo autoritativo; permite corregir
  un fragmento perdido y mantiene compatibilidad con clientes que no implementan streaming.
- **`proactive`** — aviso completo a mostrar/reproducir.
- **`approval_required`** — pausa una acción sensible y solicita decisión humana.
- **`approval_resolved`** — confirma si la acción fue autorizada o bloqueada.
- **`tool_event`** — transmite la actividad agéntica en tiempo real. `phase` progresa por
  `proposed`, `running` y `completed`, o termina en `denied`; puede incluir una vista
  previa segura de código y la salida limitada de la herramienta.

Durante una aprobación el cliente responde por el mismo WebSocket:

```json
{"type":"approval","approval_id":"<id recibido>","approved":true}
```

El identificador es efímero y solo sirve para la acción pendiente. Sin respuesta, con un
identificador incorrecto o al vencer el plazo, el gateway deniega la operación. El modelo
no puede fabricar su propia aprobación.

### API de voz local

Los clientes web capturan audio con `MediaRecorder` y usan dos endpoints autenticados:

- `POST /voice/transcribe`, cuerpo binario `audio/webm`, `audio/mp4` u otro formato
  reconocido por Whisper. Devuelve `{"text":"..."}`.
- `POST /voice/synthesize`, JSON `{"text":"..."}`. Devuelve audio WAV generado por Piper.
- `GET /voice/status` informa si la voz local está activada.

Los tres requieren `X-Jarvis-Key`. El HUD usa estos endpoints cuando están disponibles y
recurre a las capacidades de voz del navegador si el servidor los tiene desactivados.

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

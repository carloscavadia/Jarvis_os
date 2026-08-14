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
{ "type": "tool_event", "phase": "completed", "tool": "show_in_workspace", "presentation": {"title":"Informe", "content":"...", "format":"markdown", "keep_open":true} }
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
  previa segura de código, la salida limitada de la herramienta o una `presentation`
  renderizada como texto seguro. Las presentaciones con `keep_open` permanecen hasta que
  el usuario cierre la ventana.

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
- `POST /voice/synthesize`, JSON `{"text":"..."}`. Devuelve audio WAV generado por Kokoro.
- `GET /voice/status` informa si la voz local está activada.

Los tres requieren `X-Jarvis-Key`. El HUD usa estos endpoints cuando están disponibles y
recurre a las capacidades de voz del navegador si el servidor los tiene desactivados.

`GET /voice/status` incluye el bloque `wakeword`, que el cliente debe consultar antes de
abrir la escucha permanente:

```json
{"wakeword": {"enabled": true, "model": "hey_jarvis",
              "sample_rate": 16000, "frame_samples": 1280, "engine": "openwakeword"}}
```

### Música

Cuando JARVIS usa `play_music` o `control_music`, el `tool_event` (canal de texto) o el
evento `tool` (canal de voz) incluye una clave `music` con la orden para el reproductor:

```json
{"music": {"command": "play", "connector": "musica", "source": "queen",
           "queue": [{"id":"42","title":"…","artist":"…","album":"…",
                      "duration":355,"cover_art":"al-1"}]}}
{"music": {"command": "pause"}}
```

`command` puede ser `play`, `pause`, `resume`, `next`, `previous` o `stop`. El
dispositivo pide cada pista al gateway, que hace de proxy contra el servidor de música:

- `GET /music/<módulo>/stream/<id>?token=<clave>` — audio.
- `GET /music/<módulo>/cover/<id>?token=<clave>&size=256` — carátula.

La clave va por query porque un `<audio src>` no admite cabeceras, igual que en el
WebSocket del HUD.

### Escucha permanente «Hey JARVIS»

Conexión: `ws://<servidor>:8080/ws/wake/<device_id>?token=<JARVIS_GATEWAY_API_KEY>`

Es el canal que el HUD y cada punto ESP32 mantienen abierto en reposo. La detección
ocurre **en el servidor**: el dispositivo solo captura y envía.

| Sentido | Mensaje | Significado |
|---|---|---|
| → servidor | *binario* | PCM 16 bits **con signo, mono, 16 kHz, little-endian**. Cualquier tamaño; el ideal son bloques de 1280 muestras (80 ms). Máximo 81 920 bytes por mensaje. |
| → servidor | `reset` (texto) | Descarta el audio acumulado. Envíalo al reanudar la escucha tras hablar, para no activarte con tu propio eco. |
| ← cliente | `{"type":"wake_ready","model":…,"sample_rate":16000,"frame_samples":1280}` | Canal listo. |
| ← cliente | `{"type":"wake","score":0.87,"device":"hud"}` | Frase reconocida. |
| ← cliente | `{"type":"error","error":"…"}` | Detector no disponible; el servidor cierra a continuación. |

**Reglas para el dispositivo:**

1. **Deja de enviar mientras reproduces audio de JARVIS.** Si no, su voz reactiva la escucha.
2. Al reanudar, manda `reset` antes de volver a enviar.
3. Si el zócalo se cierra, reintenta con espera (el HUD usa 3 s).

Códigos de cierre: `1008` credenciales o identificador inválidos, `1013` escucha
desactivada o límite de dispositivos alcanzado, `1009` fragmento excesivo.

### Conversación por voz (OpenAI Realtime)

Conexión: `ws://<servidor>:8080/ws/voice/<session_id>?token=<JARVIS_GATEWAY_API_KEY>`

Se abre **después** de la activación, nunca en reposo. El gateway mantiene la
sesión con OpenAI; el dispositivo solo captura y reproduce.

| Sentido | Mensaje | Significado |
|---|---|---|
| → servidor | *binario* | PCM 16 bits con signo, mono, **24 kHz**, little-endian. |
| → servidor | `{"type":"approval","approval_id":…,"approved":true}` | Decisión sobre una acción sensible. |
| → servidor | `{"type":"cancel"}` | Corta lo que JARVIS esté diciendo. |
| ← cliente | `{"type":"voice_ready","sample_rate":24000,"model":…}` | Sesión lista. |
| ← cliente | *binario* | Audio de JARVIS, mismo formato. Reprodúcelo en orden. |
| ← cliente | `{"type":"state","state":"listening\|thinking\|idle"}` | Estado para la animación. |
| ← cliente | `{"type":"transcript","text":…}` | Lo que entendió de ti. |
| ← cliente | `{"type":"reply_delta","delta":…}` · `{"type":"reply_done","text":…}` | Transcripción de su respuesta. |
| ← cliente | `{"type":"tool","phase":"proposed\|running\|completed\|denied",…}` | Herramienta en curso. |
| ← cliente | `{"type":"approval_required",…}` · `{"type":"approval_resolved",…}` | Igual que en el canal de texto. |
| ← cliente | `{"type":"voice_idle_timeout"}` | Cerrada por silencio; vuelve a la escucha de activación. |

El turno lo cierra el VAD de OpenAI, así que el dispositivo **no** necesita
detectar el final de la pregunta: basta con enviar audio de forma continua
mientras la sesión esté abierta.

Códigos de cierre: `1008` credenciales inválidas, `1013` conversación desactivada,
presupuesto diario agotado o límite de sesiones, `1011` fallo al abrir la sesión.

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

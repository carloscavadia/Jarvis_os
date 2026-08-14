# Conectores de JARVIS

## Registro modular desde el HUD

El botón **Conectores** abre el administrador de módulos. Permite registrar, probar y
eliminar módulos `n8n` o `home_assistant`. La clave se envía únicamente al gateway, queda
cifrada en SQLite y nunca vuelve al navegador ni se entrega al modelo.

Antes de usarlo, activa el registro en `/opt/jarvis_os/.env`:

```dotenv
JARVIS_CONNECTORS_ENABLED=true
JARVIS_CONNECTOR_MASTER_KEY=CLAVE_ALEATORIA_DE_64_HEX
JARVIS_PROACTIVE_EVENTS_DB=/app/data/jarvis_proactive_events.db
```

Genera la clave con `openssl rand -hex 32`. No la cambies mientras existan módulos, porque
es necesaria para descifrar sus credenciales. Después reconstruye el gateway. Los módulos
se guardan en el volumen persistente, en `/app/data/jarvis_connectors.db`.

Para Gmail, Outlook, WhatsApp, Slack, Teams y Alexa registra un módulo de tipo **n8n** y
declara las acciones exactas que implementaste. Para Home Assistant puedes usar n8n o el
módulo directo con su URL base y un token de larga duración.

JARVIS recibe tres herramientas adicionales: `list_connector_modules`,
`query_connector_module` y `run_connector_module_action`. La última exige aprobación física
en el HUD. Registrar, probar o eliminar claves es una función administrativa y no puede ser
invocada por el modelo.

JARVIS usa **n8n como bus de integración**. Gmail, Outlook, WhatsApp, calendarios, Slack,
Teams, Home Assistant, Alexa y otros servicios
conservan sus credenciales dentro del almacén de credenciales de n8n; el gateway de JARVIS
solo conoce un webhook y un token independiente.

```text
HUD/voz → JARVIS → webhook n8n → correo / mensajes / domótica / APIs
                     ↑       ↓
              eventos y chat entrante
```

Para música, ver [`docs/music.md`](music.md): se configura por `.env`, no aquí.

## 1. JARVIS llama a n8n

Crea en n8n un workflow con este patrón:

```text
Webhook POST → validar cabecera → Switch(action) → nodo del servicio → Respond to Webhook
```

El webhook debe leer el cuerpo bajo `$json.body` y validar que la cabecera
`x-jarvis-connector-token` coincida con el secreto configurado. JARVIS envía:

```json
{
  "action": "gmail.search",
  "payload": {"query": "is:unread"},
  "request_id": "identificador-único",
  "source": "jarvis_os"
}
```

La respuesta debe ser `application/json` o `text/plain`. Configura el Webhook con respuesta
mediante el último nodo, añade una rama para acciones desconocidas y devuelve errores HTTP
claros. No guardes el token en nodos de código ni parámetros visibles: usa credenciales o
variables de entorno de n8n.

En el nodo **IF** de validación, la expresión de la cabecera es:

```text
{{$json.headers['x-jarvis-connector-token']}}
```

En el nodo **Switch**, la acción se obtiene con:

```text
{{$json.body.action}}
```

Las acciones permitidas se dividen en:

- `JARVIS_N8N_READ_ACTIONS`: consultas que no cambian estado, como buscar correos.
- `JARVIS_N8N_WRITE_ACTIONS`: envíos, respuestas, creación de eventos y demás cambios.
  JARVIS siempre solicita **APROBAR / DENEGAR** en el HUD antes de ejecutarlas.

```dotenv
JARVIS_N8N_READ_ACTIONS=gmail.search,gmail.read,outlook.search,outlook.calendar.list,whatsapp.unread
JARVIS_N8N_WRITE_ACTIONS=gmail.send,gmail.reply,outlook.send,outlook.calendar.create,whatsapp.send
```

### Catálogo recomendado

El nombre antes del punto identifica el servicio y el resto la operación. Solo declara una
acción cuando su rama exista y haya sido probada en el workflow.

| Servicio | Lecturas | Escrituras con aprobación |
|---|---|---|
| Gmail | `gmail.search`, `gmail.read` | `gmail.send`, `gmail.reply` |
| Outlook | `outlook.search`, `outlook.calendar.list` | `outlook.send`, `outlook.calendar.create` |
| WhatsApp | `whatsapp.unread` | `whatsapp.send` |
| Google Calendar | `googlecalendar.list` | `googlecalendar.create`, `googlecalendar.update` |
| Slack / Teams | `slack.search`, `teams.search` | `slack.send`, `teams.send` |
| Home Assistant | `homeassistant.state`, `homeassistant.history` | `homeassistant.service`, `homeassistant.scene` |
| Alexa | `alexa.devices` | `alexa.routine` |
| Genérico | `api.get` | `api.post` |

Para **Home Assistant**, n8n puede usar su REST API y un token de larga duración guardado
como credencial. Restringe las acciones a entidades y servicios autorizados; encender,
apagar, abrir, cerrar o cambiar una escena siempre se considera escritura.

Para **Alexa**, usa uno de estos puentes administrados: una integración Alexa–Home
Assistant, un skill propio que invoque un webhook o una rutina expuesta a n8n. JARVIS no
debe almacenar cookies de Amazon ni automatizar la interfaz web de Alexa.

## 2. n8n llama a JARVIS

n8n usa la cabecera `X-Jarvis-Connector-Token` y uno de estos endpoints.

### Conversación externa

`POST http://jarvis-gateway:8080/connectors/chat`

```json
{
  "connector": "whatsapp",
  "external_user": "+15551234567",
  "message": "Resume mis correos sin leer"
}
```

La respuesta contiene `reply`. La conversación externa permanece apagada hasta habilitar
`JARVIS_CONNECTOR_CHAT_ENABLED` y autorizar el hash SHA-256 de cada identidad.

## Eventos proactivos

n8n o Home Assistant pueden enviar eventos autenticados a
`POST /connectors/events` usando `X-Jarvis-Connector-Token`. JARVIS aplica una
política determinista y registra la decisión:

- `notify`: informa en el HUD sin ejecutar acciones.
- `create_goal`: crea un objetivo verificable; en modo `auto` se usa para severidad
  `critical`.
- `request_action`: solicita aprobación física en el HUD antes de invocar una acción
  incluida en `write_actions` del módulo.

Ejemplo de acción que siempre requiere aprobación:

```json
{
  "connector": "home",
  "event": "presence.detected",
  "title": "Llegada detectada",
  "text": "¿Enciendo las luces de entrada?",
  "severity": "info",
  "policy": "request_action",
  "action": "homeassistant.service",
  "payload": {
    "domain": "light",
    "service": "turn_on",
    "service_data": {"entity_id": "light.entrada"}
  }
}
```

El historial autenticado está disponible en `GET /proactive/events`. Las acciones
pendientes reaparecen en el HUD después de una recarga o reconexión.
`conector:usuario`. El identificador externo se convierte en ese hash antes de usarlo como
sesión. Las acciones sensibles pedidas desde un canal externo se bloquean porque ese canal
no puede aprobarlas físicamente.

### Evento o notificación

`POST http://jarvis-gateway:8080/connectors/events`

```json
{
  "connector": "gmail",
  "event": "new_important_message",
  "title": "Correo importante",
  "text": "Llegó un correo prioritario de Contabilidad."
}
```

El evento se difunde a los HUD conectados y a MQTT como aviso proactivo.

## 3. Configuración

Genera un secreto distinto de `JARVIS_GATEWAY_API_KEY`:

```bash
openssl rand -hex 32
```

En `/opt/jarvis_os/.env`:

```dotenv
JARVIS_CONNECTORS_ENABLED=true
JARVIS_N8N_WEBHOOK_URL=http://IP_O_HOST_N8N:5678/webhook/jarvis-connector
JARVIS_N8N_WEBHOOK_TOKEN=SECRETO_GENERADO
JARVIS_N8N_READ_ACTIONS=gmail.search,gmail.read
JARVIS_N8N_WRITE_ACTIONS=gmail.send,gmail.reply,homeassistant.service,alexa.routine
```

Para autorizar, por ejemplo, tu número de WhatsApp:

```bash
printf %s 'whatsapp:+15551234567' | sha256sum
```

Agrega el resultado (o varios separados por comas):

```dotenv
JARVIS_CONNECTOR_CHAT_ENABLED=true
JARVIS_CONNECTOR_ALLOWED_USER_HASHES=HASH_SHA256
```

Si n8n está fuera de la red privada, usa HTTPS. La URL se configura únicamente en el
servidor y no puede ser elegida por el modelo; las redirecciones y respuestas demasiado
grandes se bloquean.

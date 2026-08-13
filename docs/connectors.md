# Conectores de JARVIS

JARVIS usa **n8n como bus de integración**. Gmail, Outlook, WhatsApp y otros servicios
conservan sus credenciales dentro del almacén de credenciales de n8n; el gateway de JARVIS
solo conoce un webhook y un token independiente.

```text
HUD/voz → JARVIS → webhook n8n → Gmail / Outlook / WhatsApp / otros
                     ↑       ↓
              eventos y chat entrante
```

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
`JARVIS_CONNECTOR_CHAT_ENABLED` y autorizar el hash SHA-256 de cada identidad
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
JARVIS_N8N_WRITE_ACTIONS=gmail.send,gmail.reply
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

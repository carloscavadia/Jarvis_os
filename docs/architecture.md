# Arquitectura de JARVIS_OS

Este documento explica *cómo* está pensado el sistema y *por qué*. Es la referencia para
entender dónde encaja cada pieza.

## 1. Principio rector

JARVIS_OS separa tres planos:

- **Cerebro (`core/`)** — decide *qué* hacer. Es donde vive el LLM y el bucle de agente.
- **Cuerpo (`gateway/`)** — transporta mensajes entre el cerebro y el mundo (usuarios,
  dispositivos).
- **Extremidades (`firmware/`)** — los dispositivos físicos (ESP32, pantallas, micrófonos).

El cerebro no sabe nada de MQTT ni de HTTP. El gateway no sabe nada de LLMs. Esta
separación es la que permite cambiar cualquier plano sin tocar los demás.

## 2. El cerebro: bucle de agente

El corazón es un **bucle de uso de herramientas** (tool-use loop) contra un LLM:

```
usuario → mensaje
   │
   ▼
┌──────────────────────────────────────────────┐
│ 1. Construir prompt de sistema (personalidad, │
│    contexto, memoria relevante)               │
│ 2. Llamar al LLM con la lista de herramientas │
│ 3. ¿El LLM pidió una herramienta?             │
│      SÍ → ejecutarla, devolver resultado ─────┼──► volver a 2
│      NO → responder al usuario                │
└──────────────────────────────────────────────┘
```

Está implementado en `core/jarvis_core/agent/orchestrator.py`. Es propio (no un framework)
porque el bucle es simple y queremos control total: gating de herramientas peligrosas,
logging, streaming, memoria, etc.

### 2.1 Modelo (LLM) intercambiable

`core/jarvis_core/llm/base.py` define la interfaz `LLMProvider`. Implementaciones:

- `AnthropicProvider` — usa el SDK oficial de Anthropic. Por defecto `claude-opus-5` con
  *adaptive thinking*. Es el cerebro recomendado.
- (Futuro) `OllamaProvider` — modelos locales para privacidad/offline.

Cambiar de cerebro es cambiar una línea de configuración, no reescribir el agente.

### 2.2 Herramientas = capacidades

Todo lo que JARVIS "puede hacer" es una `Tool` (`core/jarvis_core/tools/base.py`):

```python
class Tool:
    name: str                 # p.ej. "run_shell"
    description: str          # el LLM lee esto para decidir cuándo usarla
    input_schema: dict        # JSON Schema de los parámetros
    async def run(self, **kwargs) -> str: ...
```

Se registran en un `ToolRegistry`. El registro genera automáticamente las definiciones que
el LLM necesita. **Añadir una capacidad nueva = escribir una `Tool` y registrarla.**

Herramientas de arranque (`core/jarvis_core/tools/builtin/`):

- `system_info` — hora, uso de CPU/RAM/disco del servidor.
- `run_shell` — ejecuta comandos (con lista blanca; herramienta sensible, ver seguridad).
- `remember` / `recall` — escribe y lee memoria a largo plazo.
- `list_directory` / `read_file` — inspeccionan el workspace persistente.
- `create_directory` / `create_file` — crean recursos nuevos sin sobrescribir.
- `update_file` — modifica un archivo existente después de aprobación humana.
- `install_package` — instala un paquete permitido después de aprobación humana.
- `run_python_file` — ejecuta un `.py` del workspace con aprobación, timeout y salida limitada.
- `search_web` / `fetch_web_page` — buscan y leen HTTPS público con URLs de fuente y protección SSRF.

El acceso a archivos está confinado a `JARVIS_WORKSPACE_ROOT`. Las rutas absolutas, `..`
que escapa de la raíz y enlaces simbólicos hacia el exterior se rechazan. El contenedor no
recibe acceso al sistema de archivos completo del host ni al socket de Docker.

### 2.3 Multiagente (delegación)

El orquestador puede exponer una herramienta `delegate(agent, task)` que lanza un
**subagente** con su propio prompt y su propio subconjunto de herramientas (p.ej. un agente
"casa" con acceso a domótica, un agente "investigación" con acceso a búsqueda web). El
subagente devuelve un informe; el orquestador lo integra. Esto mantiene el contexto del
agente principal pequeño y permite especialización.

> El SDK de Anthropic ofrece además *Managed Agents* (agentes gestionados por Anthropic con
> sandbox). Para JARVIS preferimos ejecutar el bucle nosotros (autoalojado en tu Proxmox),
> pero la interfaz `LLMProvider` deja la puerta abierta.

### 2.4 Memoria

Dos niveles:

- **Corto plazo** — el historial de la conversación actual (en memoria de proceso).
- **Largo plazo** — `core/jarvis_core/memory/store.py`, respaldado por SQLite. Guarda hechos,
  preferencias y notas. En el futuro: búsqueda semántica con embeddings (sqlite-vec / pgvector).

### 2.5 Proactividad (motor de tareas)

JARVIS no solo responde: también **actúa por su cuenta**. Esto lo dan dos piezas:

- **`TaskStore`** (`core/jarvis_core/tasks/store.py`) — tareas programadas persistentes en
  SQLite: recordatorios únicos ("avísame mañana a las 8") o recurrentes ("cada mañana dame
  el estado del sistema"). Sobreviven a reinicios.
- **`Scheduler`** (`core/jarvis_core/tasks/scheduler.py`) — un bucle asíncrono que corre en
  segundo plano dentro del servicio 24/7. Cada pocos segundos comprueba si hay tareas
  vencidas y, por cada una, lanza al agente con la instrucción de la tarea y **difunde el
  aviso** a todos los dispositivos y clientes conectados.

El propio JARVIS puede crear tareas con las herramientas `schedule_task`, `list_tasks` y
`cancel_task`. Así, "recuérdame llamar al médico el lunes" se convierte en una tarea real.

**En reposo no consume nada**: el scheduler solo mira el reloj; no se llama a la IA hasta
que hay una petición del usuario o una tarea vencida. Esto permite tenerlo encendido de
forma permanente sin coste (especialmente con un modelo gratuito como GLM-5.2 vía NVIDIA).

### 2.6 Cerebro intercambiable — proveedores

`core/jarvis_core/llm/factory.py` elige el proveedor según la configuración:

- **`AnthropicProvider`** — Claude (`claude-opus-5`), recomendado por capacidad.
- **`OpenAICompatibleProvider`** — cualquier API estilo OpenAI: **NVIDIA NIM** (p.ej.
  GLM-5.2 de Z.ai, gratis), **Ollama** (local, offline), etc.

El orquestador mantiene el historial en un **formato neutral** y cada proveedor lo traduce
a su formato nativo. Cambiar de cerebro **no afecta a la memoria interna** de JARVIS.

### 2.7 Voz

`core/jarvis_core/voice/` define interfaces de **STT** (voz→texto) y **TTS** (texto→voz)
para que JARVIS sea manejable por voz:

- STT: Whisper (local) o un servicio en la nube.
- TTS: Piper (local, rápido) o un servicio en la nube.

La implementación local carga ambos modelos bajo demanda: `faster-whisper` transcribe el
audio grabado por el HUD y Piper genera WAV. El modelo STT permanece en el volumen de datos
y la voz Piper se incluye en la imagen Docker, por lo que las peticiones siguientes no
recargan los modelos.

Los dispositivos ESP32 pueden capturar audio y enviarlo al gateway, que lo pasa por STT →
agente → TTS → devuelve audio al dispositivo.

## 3. El cuerpo: gateway

`gateway/` es una app **FastAPI** que expone:

- **REST** (`POST /chat`) — petición/respuesta simple.
- **WebSocket** (`/ws`) — conversación en streaming, ideal para apps y web.
- **Puente MQTT** — se suscribe a topics de dispositivos y publica respuestas. Es la vía
  natural para los ESP32 (ligero, pensado para IoT, funciona sobre 4G).

Topics MQTT (convención):

```
jarvis/device/{device_id}/in     ← el dispositivo publica lo que oye/quiere
jarvis/device/{device_id}/out    → JARVIS publica la respuesta
jarvis/device/{device_id}/status ↔ heartbeat / estado
jarvis/broadcast                 → avisos PROACTIVOS para todos los puntos de casa
```

El topic `jarvis/broadcast` es la vía por la que un recordatorio o alerta (disparado por el
scheduler) llega a la vez a todos los puntos de voz tipo Alexa repartidos por la casa. Los
clientes WebSocket conectados también reciben estos avisos.

## 4. Las extremidades: firmware ESP32

La LilyGo **T-A7670G R2** combina un ESP32 con un módem **4G LTE Cat-1 (SIMCom A7670G)**.
Esto permite dispositivos que funcionan **fuera de la red local** (en el coche, en otra
casa) hablando con tu servidor por Internet móvil.

Flujo típico de un dispositivo:

1. Arranca, conecta por 4G (o WiFi si está disponible).
2. Se conecta al broker MQTT del gateway (con TLS).
3. Publica en `jarvis/device/<id>/in` (texto o audio) cuando el usuario interactúa.
4. Escucha `jarvis/device/<id>/out` y muestra/reproduce la respuesta en la pantalla/altavoz.

## 5. Seguridad (desde el día 1)

- **Herramientas peligrosas con gating.** `run_shell` usa lista blanca de comandos y, en
  producción, puede requerir confirmación. Nunca ejecutar comandos arbitrarios sin control.
- **Secretos fuera del código.** Todo por variables de entorno / `.env` (nunca commiteado).
- **MQTT con TLS y credenciales.** Los dispositivos se autentican; el broker no es anónimo
  en producción.
- **Principio de menor privilegio.** El contenedor LXC en Proxmox corre sin más permisos de
  los necesarios.

## 6. Despliegue en Proxmox

Recomendado: un **contenedor LXC** (ligero) o una **VM** con Debian 12. Dentro:

- El núcleo + gateway como servicios systemd (o vía Docker Compose).
- Un broker **Mosquitto** (MQTT).
- (Opcional) Whisper/Piper para voz local.

Ver `deploy/` para los archivos concretos.

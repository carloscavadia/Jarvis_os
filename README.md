# JARVIS_OS

> Un asistente agéntico personal, autoalojado, inspirado en el JARVIS de Iron Man —
> pero construido desde cero, abierto y totalmente personalizable.

JARVIS_OS **no es un sistema operativo desde cero** (construir un kernel/distro Linux
completo es un esfuerzo enorme y con poco retorno para este objetivo). Es una **capa de
servicios agéntica** que se despliega sobre una base Linux mínima (Debian/Ubuntu) dentro
de un contenedor **LXC** o una **VM** en tu servidor **Proxmox**. El resultado, en la
práctica, se comporta como "un sistema" con el que hablas y que ejecuta tareas por ti.

Un proyecto adicional (`firmware/`) añade **interacción física**: pantallas y placas
**ESP32 (LilyGo T-A7670G R2, 4G LTE Cat-1)** que hablan con el núcleo por MQTT/WebSocket,
incluso fuera de tu red local gracias al módem 4G.

---

## Visión

```
        ┌──────────────────────────────────────────────────────────┐
        │                      TÚ (voz / texto)                     │
        └───────────────┬───────────────────────┬──────────────────┘
                        │                        │
            Dispositivos físicos          App / Web / Terminal
        (ESP32 + pantalla + micro)        (cliente que quieras)
                        │                        │
                        └──────────┬─────────────┘
                          MQTT / WebSocket / REST
                                   │
                        ┌──────────▼───────────┐
                        │   Gateway (FastAPI)  │  ← puerta de entrada
                        │  REST · WS · MQTT    │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │   Núcleo agéntico    │  ← el "cerebro"
                        │  Orquestador multi-  │
                        │  agente + tools +    │
                        │  memoria + voz       │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
        Modelos LLM           Herramientas           Memoria
      (Claude / local)     (shell, casa, APIs…)   (SQLite + vectores)
```

## Componentes

| Carpeta            | Qué es                                                                 |
|--------------------|-----------------------------------------------------------------------|
| `core/`            | El cerebro: orquestador multiagente, herramientas, memoria, tareas, voz. Python. |
| `gateway/`         | API de comunicaciones: REST + WebSocket + puente MQTT para dispositivos. |
| `clients/web-hud/` | El **HUD reactivo de JARVIS** (orbe animado) en web: cliente y referencia visual. |
| `firmware/`        | Proyecto del dispositivo: ESP32 LilyGo T-A7670G, punto de voz con pantalla. |
| `deploy/`          | Docker Compose, unidades systemd y notas de despliegue en Proxmox.    |
| `docs/`            | Arquitectura, protocolo, hoja de ruta y guía de hardware.             |

> **Son dos proyectos que se hablan por un contrato.** El *servidor* (`core/` + `gateway/`)
> y el *dispositivo/HUD* (`firmware/`, `clients/web-hud/`) evolucionan por separado y se
> comunican por el protocolo de estados de [`docs/protocol.md`](docs/protocol.md). Ese
> protocolo es lo que hace que la animación reaccione (escucha → piensa → habla).

## Filosofía de diseño

1. **Desde cero, pero pragmático.** El bucle de agente, el registro de herramientas y la
   orquestación son propios (no dependemos de un framework pesado tipo LangChain). Pero sí
   usamos SDKs oficiales para lo que no aporta valor reinventar (el SDK de Anthropic, MQTT,
   FastAPI).
2. **Cerebro intercambiable.** El LLM está detrás de una interfaz. Por defecto usa **Claude
   (`claude-opus-5`)**; puedes enchufar modelos locales (Ollama) para privacidad/offline.
3. **Herramientas = capacidades.** Cada cosa que JARVIS "puede hacer" es una herramienta
   registrada con un esquema. Añadir capacidades = añadir herramientas.
4. **Multiagente.** Un orquestador puede delegar en subagentes especializados (casa,
   investigación, código…).
5. **Physical-first.** El sistema está pensado para hablar por voz y con dispositivos, no
   solo por chat. Los ESP32 son **puntos de voz tipo Alexa** repartidos por casa, servidos
   desde el servidor central.
6. **Reactivo y proactivo.** No solo responde: un motor de tareas en segundo plano deja que
   JARVIS ejecute recordatorios y tareas periódicas por su cuenta y avise a todos los
   dispositivos. Encendido 24/7 y, en reposo, sin consumir (ideal con un modelo gratis como
   GLM-5.2 vía NVIDIA).

## Empezar

```bash
# 1. Requisitos: Python 3.11+, y una API key de Anthropic
cp deploy/.env.example .env
$EDITOR .env            # configura API del LLM y JARVIS_GATEWAY_API_KEY

# 2. Instalar el núcleo
cd core
pip install -e .

# 3. Hablar con JARVIS por terminal
jarvis chat

# 4. (Opcional) Levantar todo el stack (gateway + broker MQTT)
cd ../deploy
docker compose --env-file ../.env up -d
```

El gateway no arranca sin `JARVIS_GATEWAY_API_KEY`. Los clientes REST envían la clave
en `X-Jarvis-Key`; los HUB WebSocket usan
`ws://servidor:8080/ws/<session>?token=<clave>`. La capacidad de shell permanece
disponible únicamente en la CLI local y no se registra en sesiones remotas.

Para una instalación completa y verificable en Ubuntu Server, consulta
[docs/ubuntu-server.md](docs/ubuntu-server.md).

## Estado

Proyecto en fase de **fundación**: la arquitectura, el bucle de agente, el registro de
herramientas, la memoria y el gateway están esbozados y funcionando en su forma mínima.
Consulta [`docs/roadmap.md`](docs/roadmap.md) para las fases siguientes.

## Licencia

Ver [`LICENSE`](LICENSE) (MIT por defecto — cámbialo si lo prefieres).

# JARVIS HUD (web)

El **HUD reactivo de JARVIS** en el navegador: el orbe estilo película que cambia entre
reposo, escucha, pensamiento y habla. Es a la vez:

- un **cliente real** que se conecta al gateway y te deja hablar con JARVIS, y
- la **referencia visual** de la animación para portarla al display del ESP32.

## Uso

Es un único fichero sin dependencias. Ábrelo directamente o sírvelo:

```bash
# opción rápida: servirlo con Python
cd clients/web-hud
python3 -m http.server 5173
# abre http://localhost:5173
```

### Modo demo (sin servidor)

Al abrirlo funciona en modo demo: usa los botones **Reposo / Escucha / Pensar / Hablar**
para ver cada estado, y escribe en la consola para una respuesta simulada.

### Conectado a tu servidor

Pon la URL del WebSocket de tu gateway y pulsa **Conectar**:

```
ws://<ip-del-servidor>:8080/ws/hud
```

O pásala por query param: `index.html?ws=ws://192.168.1.100:8080/ws/hud`

A partir de ahí, lo que escribas va a JARVIS de verdad y el orbe reacciona a los estados
que emite el servidor (`listening` → `thinking` → `speaking` → `idle`). También recibe los
**avisos proactivos** que dispara el scheduler.

## Estados

Ver [`docs/protocol.md`](../../docs/protocol.md) para el contrato completo de estados y
mensajes que comparten el servidor y todos los dispositivos.

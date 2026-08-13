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

El HUD usa una dirección fija para el gateway:

```text
ws://192.168.68.100:8080/ws/hub-test
```

Introduce la clave del HUB y pulsa **Conectar**; ya no es necesario escribir ni pasar la
URL mediante parámetros.

A partir de ahí, lo que escribas va a JARVIS de verdad y el orbe reacciona a los estados
que emite el servidor (`listening` → `thinking` → `speaking` → `idle`). También recibe los
**avisos proactivos** que dispara el scheduler.

El botón **Conectores** abre el registro administrativo de módulos n8n y Home Assistant.
Permite guardar y probar claves sin exponerlas al chat; requiere
`JARVIS_CONNECTOR_MASTER_KEY` en el gateway.

### Voz y micrófono

Al conectarse, el HUD mantiene una escucha pasiva de la frase «Hey Jarvis». Ignora todo lo
demás y solo abre una interacción al oírla. Puedes decir la pregunta en la misma frase o
hacer una pausa y continuar. En ese segundo caso, la detección de actividad de voz cierra y
envía la pregunta después de 1,25 segundos de silencio. El micrófono se suspende mientras
JARVIS habla para evitar que se active con su propia voz. El botón **●** queda como control
de recuperación para conceder o reintentar el permiso si el navegador bloquea la escucha.

Si el gateway tiene voz local activa, Whisper transcribe la grabación y Kokoro reproduce la
respuesta con `em_alex`. El botón **◖** activa o silencia la salida de voz. Durante el
streaming, el HUD envía cada frase terminada a Kokoro de forma
anticipada y reproduce la cola en orden, reduciendo la espera entre texto y audio.
Mientras se reproduce Kokoro, Web Audio mide la amplitud real de la voz y sincroniza con
ella el núcleo, el halo, la expansión y la turbulencia de las partículas.

Si el servicio local no está disponible, el HUD usa el reconocimiento y la síntesis del
navegador. Chrome ofrece la mejor compatibilidad para ese fallback. El HUD debe servirse
desde `localhost` o mediante HTTPS para que el navegador permita usar el micrófono.

### Aprobaciones de acciones sensibles

Cuando JARVIS quiera modificar un archivo existente o instalar un paquete, el HUD muestra
la operación exacta y pausa el agente. Escribe **APROBAR** para permitirla o **DENEGAR**
para bloquearla. La petición vence automáticamente y nunca puede ser aprobada por el LLM.

El **Espacio de trabajo** aparece como una ventana independiente en la esquina superior
derecha cuando JARVIS usa una herramienta. Solo conserva la ejecución actual, muestra el
código Python y la salida capturada, ofrece botones físicos **APROBAR** / **DENEGAR**, puede
cerrarse manualmente y se limpia automáticamente después de completar una acción. El panel
y la consola de salida mantienen visible el evento más reciente mediante scroll automático.

JARVIS también puede invocar `show_in_workspace` por iniciativa propia o cuando le pidas,
por ejemplo: «muéstrame el resultado en el espacio de trabajo». Admite texto, código, JSON,
tablas y Markdown como texto seguro. Estas presentaciones permanecen abiertas hasta pulsar
el botón **×**; una presentación nueva reemplaza a la anterior.

## Estados

Ver [`docs/protocol.md`](../../docs/protocol.md) para el contrato completo de estados y
mensajes que comparten el servidor y todos los dispositivos.

# JARVIS HUD (web)

El **HUD reactivo de JARVIS** en el navegador: el orbe estilo película que cambia entre
reposo, escucha, pensamiento y habla. Es a la vez:

- un **cliente real** que se conecta al gateway y te deja hablar con JARVIS, y
- la **referencia visual** de la animación para portarla al display del ESP32.

## Uso

**Lo sirve el propio gateway** en `/hud`, así que el HUD y el servidor van
siempre a la par: no hay copias sueltas que se queden atrás.

```bash
# desde tu equipo, un túnel al servidor
ssh -L 8080:localhost:8080 usuario@servidor
# y abre
http://127.0.0.1:8080/hud
```

El túnel no es capricho: el micrófono solo funciona desde `localhost` o HTTPS, y
`http://IP-del-servidor:8080` no cuenta como origen seguro para Chrome.

También puedes servir el fichero por tu cuenta si prefieres:

```bash
cd clients/web-hud && python3 -m http.server 4173
```

Pero entonces eres tú quien debe mantenerlo actualizado tras cada `git pull`.

### Rendimiento del orbe

El HUD detecta equipos modestos y baja el detalle: sin campo de profundidad, sin
instrumentación angular y con un bloom simple. La detección es conservadora
—cuatro núcleos o menos—, y eso hoy incluye mini-PC perfectamente capaces. Se
puede forzar en los dos sentidos, y la preferencia se recuerda en el navegador:

```text
http://127.0.0.1:8080/hud?lowspec=0    # detalle completo
http://127.0.0.1:8080/hud?lowspec=1    # modo reducido (kiosko en Raspberry)
```

El movimiento también respeta `prefers-reduced-motion`: con esa preferencia
activa, la deriva del campo y los flujos de datos se detienen.

### Modo demo (sin servidor)

Al abrirlo funciona en modo demo: usa los botones **Reposo / Escucha / Pensar / Hablar**
para ver cada estado, y escribe en la consola para una respuesta simulada.

### Conectado a tu servidor

El HUD es un fichero que abre el navegador, así que **no puede leer el `.env` del
servidor**. Su equivalente es `config.js`, que se edita una vez donde sirves el HUD:

```bash
cd clients/web-hud
cp config.example.js config.js     # y pon ahí la clave del HUB
```

Con eso se conecta solo al abrirlo y el campo de clave queda deshabilitado. El fichero
está en `.gitignore`, así que la clave no se sube al repositorio.

Si prefieres no crearlo, el HUD **recuerda la clave en el navegador** tras la primera
conexión correcta y se reconecta solo las siguientes veces. Solo hay que escribirla una vez.

> No se sirve la clave desde el gateway a propósito: cualquiera que alcanzase esa ruta
> la obtendría, y eso convertiría un gateway autenticado en uno abierto para toda la red.

A partir de ahí, lo que escribas va a JARVIS de verdad y el orbe reacciona a los estados
que emite el servidor (`listening` → `thinking` → `speaking` → `idle`). También recibe los
**avisos proactivos** que dispara el scheduler.

El botón **Conectores** abre el registro administrativo de módulos n8n y Home Assistant.
Permite guardar y probar claves sin exponerlas al chat; requiere
`JARVIS_CONNECTOR_MASTER_KEY` en el gateway.

Cuando JARVIS recibe una solicitud compleja, puede crear un objetivo persistente. El HUD
muestra una tarjeta de misión en la esquina superior izquierda con pasos y avance. Cada paso
solo se completa después de guardar evidencia, y el objetivo no puede cerrarse como exitoso
si queda trabajo sin verificar.

### Voz y micrófono

Al conectarse, el HUD mantiene una escucha pasiva de la frase «Hey JARVIS». Ignora todo lo
demás y solo abre una interacción al oírla. Tras oírla, haz la pregunta: la detección de
actividad de voz la cierra y la envía después de 1,25 segundos de silencio. El micrófono
deja de enviar mientras JARVIS habla, para que no se active con su propia voz. El botón
**●** reconstruye la escucha si se queda colgada o si acabas de conceder el permiso. Al
reconocer la frase reproduce un tono ascendente corto tipo “bluuup”; el tono respeta el
control de silencio **◖**.

**La detección es local.** El micrófono se convierte a PCM de 16 kHz en un AudioWorklet y
viaja por WebSocket al gateway, que lo evalúa con openWakeWord. No interviene el
reconocimiento de voz del navegador, así que el audio de reposo **no sale de tu red**.
Requiere `JARVIS_VOICE_ENABLED=true` y `JARVIS_WAKEWORD_ENABLED=true`; si no, el HUD lo
indica con `ESCUCHA NO DISPONIBLE` y el botón **●** pasa a grabación manual.

Si no te reconoce o se activa sola, ajusta el umbral midiendo tu propia voz — ver
[`docs/voice.md`](../../docs/voice.md#ajustar-el-umbral-con-tu-voz).

### Conversación Realtime

Si el gateway tiene `JARVIS_REALTIME_CONVERSATION_ENABLED=true`, al activarse el HUD
abre una conversación por voz con OpenAI Realtime **a través del servidor**. A partir
de ahí hablas con normalidad: no hay que pulsar para enviar ni esperar turnos, porque
el final de cada pregunta lo cierra el VAD de OpenAI y puedes interrumpirle mientras
habla. Las herramientas se ejecutan en el gateway, así que las acciones sensibles
piden aprobación en el mismo panel de siempre.

Mientras dura la conversación, Kokoro queda en silencio: el audio ya viene hecho, y
sintetizarlo otra vez haría que JARVIS se hablara encima. Al cerrarse por silencio
—90 s por defecto— el HUD vuelve solo a la escucha de «Hey JARVIS».

Si el gateway tiene voz local activa, Whisper transcribe la grabación y Kokoro reproduce la
respuesta con `em_alex`. El botón **◖** activa o silencia la salida de voz. Durante el
streaming, el HUD envía cada frase terminada a Kokoro de forma
anticipada y reproduce la cola en orden, reduciendo la espera entre texto y audio.
Mientras se reproduce Kokoro, Web Audio mide la amplitud real de la voz y sincroniza con
ella el núcleo, el halo, la expansión y la turbulencia de las partículas.

Si el servicio local no está disponible, el HUD recurre al reconocimiento y la síntesis del
navegador **solo para la pregunta**, nunca para la escucha permanente. Chrome ofrece la
mejor compatibilidad para ese respaldo. El HUD debe servirse desde `localhost` o mediante
HTTPS para que el navegador permita usar el micrófono.

### Reproductor de música

Si el gateway tiene `JARVIS_NAVIDROME_URL` configurado, pedirle a JARVIS «pon música» o «pon algo de
Queen» abre un reproductor en la **esquina inferior derecha**. Tiene anterior, pausa,
siguiente, cierre, barra de progreso y carátula.

También puedes decirle «pausa», «siguiente» o «para la música» y responde sobre el
reproductor ya abierto.

**La música baja de volumen sola mientras JARVIS habla** y vuelve a subir al terminar,
así que no hace falta pausarla para preguntarle algo. Funciona con las tres rutas de voz
(Kokoro, Realtime y la del navegador).

El audio pasa por el gateway, que hace de proxy contra Navidrome: el navegador nunca
recibe las credenciales del servidor de música.

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

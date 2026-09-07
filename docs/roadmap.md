# Hoja de ruta de JARVIS_OS

Fases pensadas para ir de "funciona en mi terminal" a "mi JARVIS físico responde por voz
desde el coche". Cada fase deja algo usable.

## Fase 0 — Fundación ✅ (este repo)
- [x] Estructura de monorepo y documentación de arquitectura.
- [x] Núcleo: bucle de agente con uso de herramientas (reactivo).
- [x] **Multiproveedor de IA**: Claude (`claude-opus-5`) y compatible con OpenAI
      (NVIDIA NIM / GLM-5.2, Ollama…). La memoria interna es propia e independiente.
- [x] Registro de herramientas + herramientas de arranque (system_info, shell, memoria).
- [x] **Motor proactivo**: almacén de tareas persistente + scheduler en segundo plano +
      herramientas para que JARVIS se programe recordatorios/tareas periódicas.
- [x] Memoria a largo plazo con SQLite.
- [x] CLI `jarvis chat` para hablar por terminal.
- [x] Gateway FastAPI 24/7 con REST + WebSocket + puente MQTT + difusión proactiva.
- [x] Firmware ESP32 (esqueleto PlatformIO) para LilyGo T-A7670G, como punto de voz.
- [x] Despliegue: docker-compose + systemd + notas Proxmox.

## Fase 1 — Cerebro sólido
- [x] Streaming de respuestas (texto incremental) por WebSocket y HUD tipo karaoke,
      real también con Claude (antes se entregaba la respuesta de una vez).
- [x] Prompt de personalidad configurable en caliente (`PUT /persona`), sin reiniciar
      y sin invalidar la caché del modelo.
- [x] Caché de prompt: el prefijo estable (herramientas + prompt de sistema) se
      cachea, y lo que cambia cada turno va en una capa aparte.
- [x] Modo offline verificable (`JARVIS_OFFLINE`): apaga lo que sale a internet y
      `/ready` confirma si se cumple. Ollama ya funcionaba por el proveedor
      compatible con OpenAI, así que no hacía falta un proveedor propio.
- [x] Suite de tests del bucle de agente y del registro de herramientas.

## Fase 2 — Voz
- [x] Integrar STT (Whisper) y TTS (Kokoro con `em_alex`) locales.
- [x] Pipeline web de voz extremo a extremo: audio → texto → agente → texto → audio.
- [x] Detección de palabra de activación ("Hey JARVIS") 100% local con openWakeWord.

## Fase 3 — Capacidades (herramientas)
- [x] Motor de políticas (allow/ask/deny por herramienta y argumentos, denegaciones
      críticas no configurables, concesiones de sesión) + auditoría append-only.
- [x] Concesiones de sesión cableadas en el HUD ("aprobar en esta conversación") y
      endpoint `GET /audit` para consultar la bitácora.
- [x] `jarvis-node`: agente compañero en cada máquina (Windows/macOS/Linux) que conecta
      hacia afuera por MQTT y expone operaciones tipadas en vez de cadenas de shell.
      Manifiesto publicado por el nodo, confinamiento de rutas, prohibiciones absolutas
      y modo de solo lectura por defecto.
- [x] Lado del gateway: los nodos se descubren solos por su manifiesto (MQTT) y el
      agente los usa con `list_nodes` y `node_operation`, sobre el motor de políticas
      (el alcance de una concesión es `nodo:operación`).
- [ ] Control gráfico por visión (captura → modelo → coordenadas), solo para lo que no
      tiene API.
- [x] Workspace seguro: crear, listar, leer y actualizar archivos/carpetas.
- [x] Instalación limitada de paquetes con aprobación humana interactiva.
- [x] Domótica: Home Assistant (entidades, estado y servicios) como módulo de
      conector, con matriz táctil en el HUD.
- [x] Búsqueda web y lectura HTTPS pública con protección SSRF y fuentes visibles.
- [x] Navegador real que JARVIS conduce paso a paso (Chromium headless): busca, entra
      en un resultado, lee, vuelve. Se ve en una ventana del HUD y el usuario puede
      pinchar sobre la misma pestaña. Opcional y apagado por defecto.
- [x] Visor de ventanas flotantes: imagen, PDF, documento del workspace, vídeo de
      YouTube y página web. Se arrastran, se redimensionan y conviven con el pizarrón.
- [x] Correo saliente por SMTP (`send_email`), con aprobación humana.
- [x] Calendario: agenda local con eventos, consulta por rango y vista en el
      mini calendario del HUD. Los recordatorios ya los cubre el motor de tareas.
- [x] Lectura del servidor Proxmox desde el agente: estado de los nodos y lista de
      VMs y contenedores. (Antes solo lo pintaba el HUD; el agente no tenía
      herramienta y no podía responder por Proxmox.)
- [ ] Control del propio servidor Proxmox (arrancar/parar VMs y LXC).
- [ ] Que el navegador rellene formularios de varios pasos por su cuenta (hoy escribe
      en un campo y pulsa Enter; encadenar un alta entera aún lo dirige el usuario).
- [x] Multiagente: subagentes "casa", "investigación", "código" y "agenda", cada uno
      con su propio conjunto acotado de herramientas. Heredan las aprobaciones y
      no pueden volver a delegar.
- [x] Motor de objetivos persistentes con planes, verificación y progreso en el HUD.

## Fase 4 — Dispositivos físicos
- [ ] Firmware: conexión MQTT sobre 4G con TLS.
- [ ] Pantalla: mostrar respuestas y estado.
- [ ] Micrófono + altavoz: interacción por voz desde el dispositivo.
- [ ] Aprovisionamiento sencillo de dispositivos nuevos (ID + credenciales).

## Fase 5 — Autonomía
- [x] Tareas programadas / proactivas ("avísame si…", "cada mañana…"), con aviso
      por correo desde `send_email`.
- [x] Los hechos recordados se le ponen delante al modelo en cada turno, sin que
      tenga que consultarlos.
- [x] Memoria semántica con embeddings: busca por significado. Modelo local
      (nada sale de la red) o endpoint compatible como NVIDIA NIM.
- [x] Saber dónde está el usuario, leyendo `person.*`/`device_tracker.*` de Home
      Assistant, con la dirección fija del `.env` como respaldo y la procedencia
      siempre a la vista. Por IP no se hace: dentro de la red solo hay una
      dirección privada y desde fuera sale la del operador.
- [ ] Rutas y tiempo de viaje sobre OpenStreetMap partiendo de esa ubicación.
- [ ] Aprendizaje de preferencias del usuario a lo largo del tiempo.
- [ ] Panel web de administración.

## Fase 6 — Que se resuelva solo
- [x] Conocerse a sí mismo: qué es y con qué cerebro piensa, qué puede hacer y qué
      le falta a cada capacidad, qué habilidades ha aprendido y con qué acierto, a
      quién puede delegar, cuánto recuerda y cuáles son sus límites.
- [x] Método ante lo no programado: averiguar antes de rendirse, decir qué falta
      exactamente, y guardar lo resuelto como habilidad o memoria.
- [ ] Que proponga y registre servidores MCP por su cuenta cuando la capacidad
      exista ahí fuera. (Para APIs HTTP planas ya no hace falta: lo cubre el
      conector genérico; esto queda para servidores MCP de verdad.)
- [x] Conector HTTP genérico: cualquier API de la red se registra desde el HUD
      (tipo `http`) declarando la URL base, la cabecera de autenticación y las
      rutas exactas permitidas, separando lectura de escritura. Ya no hace falta
      escribir un módulo nuevo en Python por cada servicio.
- [x] Pedir lo que le falta (`request_from_user`): el HUD lo presenta como una
      tarjeta accionable, con el dónde separado del qué. Un secreto nunca se pide
      por el chat: acabaría en el historial y viajaría al modelo cada turno.
- [ ] Que detecte credenciales configuradas sin herramienta que las use y lo avise.

## Ideas / backlog
- Integración con cámaras (visión).
- Reconocimiento de voz por hablante (quién habla).
- Modo "presencia" (JARVIS sabe quién está en casa).
- Federación de varios nodos JARVIS.

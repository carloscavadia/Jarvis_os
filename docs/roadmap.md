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
- [x] Workspace seguro: crear, listar, leer y actualizar archivos/carpetas.
- [x] Instalación limitada de paquetes con aprobación humana interactiva.
- [x] Domótica: Home Assistant (entidades, estado y servicios) como módulo de
      conector, con matriz táctil en el HUD.
- [x] Búsqueda web y lectura HTTPS pública con protección SSRF y fuentes visibles.
- [x] Correo saliente por SMTP (`send_email`), con aprobación humana.
- [x] Calendario: agenda local con eventos, consulta por rango y vista en el
      mini calendario del HUD. Los recordatorios ya los cubre el motor de tareas.
- [x] Lectura del servidor Proxmox desde el agente: estado de los nodos y lista de
      VMs y contenedores. (Antes solo lo pintaba el HUD; el agente no tenía
      herramienta y no podía responder por Proxmox.)
- [ ] Control del propio servidor Proxmox (arrancar/parar VMs y LXC).
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
- [ ] Aprendizaje de preferencias del usuario a lo largo del tiempo.
- [ ] Panel web de administración.

## Fase 6 — Que se resuelva solo
- [x] Introspección: JARVIS sabe qué puede hacer y qué le falta, y distingue
      «no existe» de «está apagado» de «falta una credencial».
- [x] Método ante lo no programado: averiguar antes de rendirse, decir qué falta
      exactamente, y guardar lo resuelto como habilidad o memoria.
- [ ] Que proponga y registre servidores MCP por su cuenta cuando la capacidad
      exista ahí fuera.
- [x] Pedir lo que le falta (`request_from_user`): el HUD lo presenta como una
      tarjeta accionable, con el dónde separado del qué. Un secreto nunca se pide
      por el chat: acabaría en el historial y viajaría al modelo cada turno.
- [ ] Que detecte credenciales configuradas sin herramienta que las use y lo avise.

## Ideas / backlog
- Integración con cámaras (visión).
- Reconocimiento de voz por hablante (quién habla).
- Modo "presencia" (JARVIS sabe quién está en casa).
- Federación de varios nodos JARVIS.

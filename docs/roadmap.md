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
- [ ] Streaming de respuestas (texto token a token) por WebSocket.
- [ ] Prompt de personalidad configurable (nombre, tono, idioma, "reglas de la casa").
- [ ] Caché de prompt para bajar coste/latencia.
- [ ] Proveedor `OllamaProvider` para modelos locales (offline/privacidad).
- [ ] Suite de tests del bucle de agente y del registro de herramientas.

## Fase 2 — Voz
- [ ] Integrar STT (Whisper) y TTS (Piper) locales.
- [ ] Pipeline de voz extremo a extremo: audio → texto → agente → texto → audio.
- [ ] Detección de palabra de activación ("Jarvis").

## Fase 3 — Capacidades (herramientas)
- [ ] Domótica: Home Assistant (encender luces, leer sensores).
- [ ] Búsqueda web y resumen.
- [ ] Calendario y recordatorios.
- [ ] Control del propio servidor Proxmox (estado de VMs/LXC, arrancar/parar).
- [ ] Multiagente: subagentes "casa", "investigación", "código".

## Fase 4 — Dispositivos físicos
- [ ] Firmware: conexión MQTT sobre 4G con TLS.
- [ ] Pantalla: mostrar respuestas y estado.
- [ ] Micrófono + altavoz: interacción por voz desde el dispositivo.
- [ ] Aprovisionamiento sencillo de dispositivos nuevos (ID + credenciales).

## Fase 5 — Autonomía
- [ ] Tareas programadas / proactivas ("avísame si…", "cada mañana…").
- [ ] Memoria semántica con embeddings (búsqueda por significado).
- [ ] Aprendizaje de preferencias del usuario a lo largo del tiempo.
- [ ] Panel web de administración.

## Ideas / backlog
- Integración con cámaras (visión).
- Reconocimiento de voz por hablante (quién habla).
- Modo "presencia" (JARVIS sabe quién está en casa).
- Federación de varios nodos JARVIS.

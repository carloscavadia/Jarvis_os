# Voz de JARVIS

JARVIS mantiene la escucha siempre activa con motores **locales**. Opcionalmente puede
usar OpenAI Realtime únicamente para producir una voz más natural después de detectar
«Hey JARVIS» y obtener una respuesta. Así no hay conexión ni consumo continuo.

| Función | Motor | Notas |
|---|---|---|
| Oír (STT) | **faster-whisper** | Modelo `small` por defecto; buen equilibrio en español sobre CPU. |
| Hablar (TTS) | **Kokoro-82M** | Voz `em_alex`; natural y rápida en CPU. |
| Hablar (opcional) | **OpenAI Realtime** | Voz `marin` bajo demanda; Kokoro queda como respaldo automático. |

## Por qué Kokoro

**Kokoro-82M** (82 M de parámetros, Apache 2.0, ~330 MB) ofrece una voz natural y sigue
corriendo en CPU: el punto dulce para un asistente encendido 24/7 sin coste por uso.

## Instalación

Kokoro necesita **`espeak-ng`**, el fonemizador que usa para español. Sin él, la síntesis
falla con un error explícito.

### Con Docker (recomendado)

No hay que hacer nada: la imagen ya instala `espeak-ng` y las dependencias.

```bash
cd deploy && docker compose --env-file ../.env up -d --build
```

### En Ubuntu Server directo

```bash
sudo apt install espeak-ng
pip install "./core[voice]"
```

> La primera síntesis **descarga los pesos de Kokoro (~330 MB)**. Tarda un poco; a partir
> de ahí queda en caché (`HF_HOME`, que en Docker es un volumen persistente).

## Configuración

```bash
JARVIS_VOICE_ENABLED=true

JARVIS_TTS_VOICE=em_alex     # voz masculina predeterminada
JARVIS_TTS_LANG_CODE=e       # 'e' = español
JARVIS_TTS_SPEED=1.0         # 0.9 más pausado · 1.1 más ágil
```

### Voces en español

| Voz | Género |
|---|---|
| `ef_dora` | Femenina |
| `em_alex` | Masculina |
| `em_santa` | Masculina |

`em_alex` es el valor predeterminado para una voz masculina serena. Cambia la variable y
reinicia el gateway si quieres probar otra voz; no hace falta tocar código.

## OpenAI Realtime bajo demanda

El navegador solicita al gateway un secreto efímero y establece WebRTC directamente con
OpenAI. La clave `OPENAI_API_KEY` nunca llega al HUD. JARVIS envía únicamente el texto que
va a pronunciar: wake word, detección de fin de frase y transcripción continúan locales.

```bash
OPENAI_API_KEY=sk-...
JARVIS_OPENAI_REALTIME_ENABLED=true
JARVIS_OPENAI_REALTIME_MODEL=gpt-realtime-2.1-mini
JARVIS_OPENAI_REALTIME_VOICE=marin
JARVIS_OPENAI_REALTIME_SESSION_SECONDS=45
JARVIS_OPENAI_REALTIME_DAILY_SESSIONS=50
JARVIS_OPENAI_REALTIME_MAX_OUTPUT_TOKENS=700
```

Cada respuesta reutiliza una sola conexión breve y la cierra al terminar. Si falta la
clave, se alcanza el límite diario o WebRTC falla, el HUD cambia automáticamente a Kokoro
y, como último respaldo, a la voz del navegador.

Para activar los cambios en el servidor:

```bash
cd /opt/jarvis_os
git pull --ff-only
cd deploy
sudo docker compose up -d --build
```

## Diagnóstico

| Síntoma | Causa probable | Solución |
|---|---|---|
| Error "Comprueba que 'espeak-ng' esté instalado" | Falta el fonemizador | `sudo apt install espeak-ng` |
| Suena **aguda/grave o acelerada** | Desajuste de frecuencia en el cliente | El WAV es **PCM 16 bits mono a 24 kHz**; asegúrate de que el reproductor respeta la cabecera. |
| La primera respuesta tarda mucho | Descarga inicial de pesos | Normal; solo la primera vez. |
| Voz cortada o sin audio | Texto vacío tras limpiar Markdown | `prepare_speech_text` elimina código y enlaces; revisa qué se envía. |

## Arquitectura

Kokoro cumple la interfaz `TextToSpeech` (`core/jarvis_core/voice/base.py`) y se construye
mediante `build_tts()` (`core/jarvis_core/voice/factory.py`). El agente y el gateway quedan
desacoplados de los detalles del modelo.

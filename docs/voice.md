# Voz de JARVIS

JARVIS oye y habla con motores **locales**: nada de audio sale de tu servidor y no hay
coste por uso.

| Función | Motor | Notas |
|---|---|---|
| Oír (STT) | **faster-whisper** | Modelo `small` por defecto; buen equilibrio en español sobre CPU. |
| Hablar (TTS) | **Kokoro-82M** | Voz `em_alex`; natural y rápida en CPU. |

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

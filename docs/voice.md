# Voz de JARVIS

JARVIS oye y habla con motores **locales**: nada de audio sale de tu servidor y no hay
coste por uso.

| Función | Motor | Notas |
|---|---|---|
| Oír (STT) | **faster-whisper** | Modelo `small` por defecto; buen equilibrio en español sobre CPU. |
| Hablar (TTS) | **Kokoro-82M** | Por defecto. Natural y rápido en CPU. |
| Hablar (alternativa) | **Piper** | Ultraligero pero robótico. Solo para hardware muy limitado. |

## Por qué Kokoro y no Piper

Piper está pensado para Raspberry Pi: prioriza tamaño y velocidad sobre naturalidad, y
suena claramente sintético. **Kokoro-82M** (82 M de parámetros, Apache 2.0, ~330 MB) suena
mucho más natural y sigue corriendo con soltura en CPU — el punto dulce para un asistente
encendido 24/7 sin coste.

## Instalación

Kokoro necesita **`espeak-ng`**, el fonemizador que usa para español. Sin él, la síntesis
falla con un error explícito.

### Con Docker (recomendado)

No hay que hacer nada: la imagen ya instala `espeak-ng` y las dependencias.

```bash
cd deploy && docker compose up -d --build
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

JARVIS_TTS_ENGINE=kokoro     # kokoro | piper
JARVIS_TTS_VOICE=ef_dora     # voz (ver tabla abajo)
JARVIS_TTS_LANG_CODE=e       # 'e' = español
JARVIS_TTS_SPEED=1.0         # 0.9 más pausado · 1.1 más ágil
```

### Voces en español

| Voz | Género |
|---|---|
| `ef_dora` | Femenina |
| `em_alex` | Masculina |
| `em_santa` | Masculina |

Para un JARVIS clásico (voz masculina serena), prueba `em_alex`. Cambia la variable y
reinicia el gateway — no hace falta tocar código.

### Volver a Piper

```bash
pip install "./core[voice-piper]"
```

```bash
JARVIS_TTS_ENGINE=piper
JARVIS_TTS_MODEL_PATH=/app/voices/es_MX-ald-medium.onnx
```

## Diagnóstico

| Síntoma | Causa probable | Solución |
|---|---|---|
| Error "Comprueba que 'espeak-ng' esté instalado" | Falta el fonemizador | `sudo apt install espeak-ng` |
| La voz suena **robótica** | Estás en Piper | `JARVIS_TTS_ENGINE=kokoro` |
| Suena **aguda/grave o acelerada** | Desajuste de frecuencia en el cliente | El WAV es **PCM 16 bits mono a 24 kHz**; asegúrate de que el reproductor respeta la cabecera. |
| La primera respuesta tarda mucho | Descarga inicial de pesos | Normal; solo la primera vez. |
| Voz cortada o sin audio | Texto vacío tras limpiar Markdown | `prepare_speech_text` elimina código y enlaces; revisa qué se envía. |

## Arquitectura

Todos los motores cumplen la interfaz `TextToSpeech` (`core/jarvis_core/voice/base.py`),
y `build_tts()` (`core/jarvis_core/voice/factory.py`) elige según configuración. Añadir un
motor nuevo (por ejemplo ElevenLabs en la nube) es escribir una clase con un método
`synthesize()` y registrarla en la fábrica: ni el agente ni el gateway cambian.

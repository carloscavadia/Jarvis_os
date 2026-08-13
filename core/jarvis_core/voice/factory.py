"""Fábrica de motores de voz.

Permite cambiar el motor TTS por configuración (`JARVIS_TTS_ENGINE`) sin tocar el
gateway ni el agente:

    kokoro  → Kokoro-82M, local y mucho más natural (recomendado).
    piper   → Piper, ultraligero pero robótico (para hardware muy limitado).
"""

from __future__ import annotations

from jarvis_core.config import Settings
from jarvis_core.voice.base import TextToSpeech
from jarvis_core.voice.local import KokoroTTS, LocalVoiceError, PiperTTS


def build_tts(settings: Settings) -> TextToSpeech:
    engine = (settings.tts_engine or "kokoro").strip().lower()

    if engine == "kokoro":
        return KokoroTTS(
            voice=settings.tts_voice or "ef_dora",
            lang_code=settings.tts_lang_code or "e",
            speed=settings.tts_speed,
            repo_id=settings.tts_kokoro_repo or None,
        )

    if engine == "piper":
        if not settings.tts_model_path:
            raise LocalVoiceError(
                "Piper necesita JARVIS_TTS_MODEL_PATH con la ruta del modelo .onnx."
            )
        return PiperTTS(settings.tts_model_path, use_cuda=settings.tts_use_cuda)

    raise LocalVoiceError(
        f"Motor TTS desconocido: '{settings.tts_engine}'. Usa 'kokoro' o 'piper'."
    )

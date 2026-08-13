"""Construcción del sintetizador local Kokoro."""

from __future__ import annotations

from jarvis_core.config import Settings
from jarvis_core.voice.base import TextToSpeech
from jarvis_core.voice.local import KokoroTTS


def build_tts(settings: Settings) -> TextToSpeech:
    return KokoroTTS(
        voice=settings.tts_voice or "em_alex",
        lang_code=settings.tts_lang_code or "e",
        speed=settings.tts_speed,
        repo_id=settings.tts_kokoro_repo or None,
    )

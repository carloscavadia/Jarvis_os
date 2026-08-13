"""Voz de JARVIS: interfaces (STT/TTS) y motores locales."""

from jarvis_core.voice.base import SpeechToText, TextToSpeech
from jarvis_core.voice.local import (
    FasterWhisperSTT,
    KokoroTTS,
    LocalVoiceError,
    PiperTTS,
)
from jarvis_core.voice.factory import build_tts

__all__ = [
    "SpeechToText",
    "TextToSpeech",
    "FasterWhisperSTT",
    "KokoroTTS",
    "PiperTTS",
    "LocalVoiceError",
    "build_tts",
]

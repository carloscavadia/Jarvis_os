"""Voz de JARVIS: interfaces (STT/TTS) y motores locales."""

from jarvis_core.voice.base import SpeechToText, TextToSpeech
from jarvis_core.voice.factory import build_tts
from jarvis_core.voice.local import FasterWhisperSTT, KokoroTTS, LocalVoiceError

__all__ = [
    "FasterWhisperSTT",
    "KokoroTTS",
    "LocalVoiceError",
    "SpeechToText",
    "TextToSpeech",
    "build_tts",
]

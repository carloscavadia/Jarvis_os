"""Voz de JARVIS: interfaces (STT/TTS) y motores locales."""

from jarvis_core.voice.base import SpeechToText, TextToSpeech
from jarvis_core.voice.factory import build_tts
from jarvis_core.voice.local import FasterWhisperSTT, KokoroTTS, LocalVoiceError
from jarvis_core.voice.wakeword import WakeWordDetector

__all__ = [
    "FasterWhisperSTT",
    "KokoroTTS",
    "LocalVoiceError",
    "SpeechToText",
    "TextToSpeech",
    "WakeWordDetector",
    "build_tts",
]

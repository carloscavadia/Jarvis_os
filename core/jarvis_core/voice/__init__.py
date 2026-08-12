"""Interfaces de voz (STT y TTS). Implementaciones concretas en fases posteriores."""

from jarvis_core.voice.base import SpeechToText, TextToSpeech

__all__ = ["SpeechToText", "TextToSpeech"]
from jarvis_core.voice.local import FasterWhisperSTT, LocalVoiceError, PiperTTS

__all__ = ["FasterWhisperSTT", "LocalVoiceError", "PiperTTS"]

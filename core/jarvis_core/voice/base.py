"""Interfaces de voz.

Definen el contrato para convertir voz↔texto sin atar el sistema a un motor concreto.
Implementaciones: Whisper (STT) y Kokoro (TTS), locales, en `docs/roadmap.md`
(Fase 2). Un dispositivo ESP32 captura audio → STT → agente → TTS → audio de vuelta.
"""

from __future__ import annotations

from typing import Protocol


class SpeechToText(Protocol):
    async def transcribe(
        self, audio: bytes, *, sample_rate: int = 16000, language: str | None = None
    ) -> str:
        """Convierte audio PCM en texto."""
        ...


class TextToSpeech(Protocol):
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """Convierte texto en audio (PCM/WAV)."""
        ...

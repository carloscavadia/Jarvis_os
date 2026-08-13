"""Ensamblaje opcional de los motores locales de voz del gateway."""

from __future__ import annotations

import html
import re

from jarvis_core.config import Settings
from jarvis_core.voice import (
    FasterWhisperSTT,
    LocalVoiceError,
    TextToSpeech,
    build_tts,
)


def prepare_speech_text(text: str) -> str:
    """Convierte Markdown de respuesta en texto natural para el sintetizador."""
    spoken = html.unescape(text)
    spoken = re.sub(r"```[\s\S]*?```", " ", spoken)
    spoken = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", spoken)
    spoken = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", spoken)
    spoken = re.sub(r"<https?://[^>]+>", " ", spoken)
    spoken = re.sub(r"https?://\S+", " ", spoken)
    spoken = re.sub(r"`([^`]*)`", r"\1", spoken)
    spoken = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s+", "", spoken)
    spoken = re.sub(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", " ", spoken)
    spoken = spoken.replace("|", ". ")
    spoken = re.sub(r"[*_~#`]", "", spoken)
    spoken = re.sub(r"[{}\[\]\\]", " ", spoken)
    spoken = re.sub(r"[•▪◦►▶]+", " ", spoken)
    spoken = re.sub(r"\s+", " ", spoken)
    spoken = re.sub(r"\s+([,.;:!?])", r"\1", spoken)
    return spoken.strip()


class VoiceRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stt: FasterWhisperSTT | None = None
        self._tts: TextToSpeech | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.voice_enabled

    def _tts_label(self) -> str:
        return f"kokoro:{self.settings.tts_voice}"

    def status(self) -> dict[str, str | bool]:
        return {
            "enabled": self.enabled,
            "stt": self.settings.stt_model if self.enabled else "disabled",
            "tts": self._tts_label() if self.enabled else "disabled",
        }

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise LocalVoiceError(
                "La voz local está desactivada (JARVIS_VOICE_ENABLED=false)."
            )

    async def transcribe(self, audio: bytes) -> str:
        self._require_enabled()
        if self._stt is None:
            self._stt = FasterWhisperSTT(
                self.settings.stt_model,
                device=self.settings.stt_device,
                compute_type=self.settings.stt_compute_type,
                download_root=self.settings.stt_download_root,
            )
        return await self._stt.transcribe(audio, language=self.settings.language)

    async def synthesize(self, text: str) -> bytes:
        self._require_enabled()
        if self._tts is None:
            self._tts = build_tts(self.settings)
        return await self._tts.synthesize(prepare_speech_text(text))

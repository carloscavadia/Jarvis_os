"""Motores locales de voz con carga diferida para no ralentizar el gateway."""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from pathlib import Path
from typing import Any


class LocalVoiceError(RuntimeError):
    """Error de configuración o ejecución de un motor de voz local."""


class FasterWhisperSTT:
    """Transcripción local mediante faster-whisper, cargado en el primer uso."""

    def __init__(
        self,
        model_name: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self._model: Any = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise LocalVoiceError(
                        "faster-whisper no está instalado; usa la dependencia voice."
                    ) from exc
                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self.download_root,
                )
        return self._model

    def _transcribe_sync(self, audio: bytes, language: str | None) -> str:
        with self._inference_lock:
            model = self._get_model()
            segments, _ = model.transcribe(
                io.BytesIO(audio),
                language=language,
                beam_size=3,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()

    async def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> str:
        del sample_rate  # El contenedor de audio declara su propia frecuencia.
        if not audio:
            return ""
        return await asyncio.to_thread(self._transcribe_sync, audio, language)


class PiperTTS:
    """Síntesis WAV local mediante Piper, con el modelo residente en memoria."""

    def __init__(self, model_path: str, *, use_cuda: bool = False) -> None:
        self.model_path = Path(model_path)
        self.use_cuda = use_cuda
        self._voice: Any = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _get_voice(self) -> Any:
        if self._voice is not None:
            return self._voice
        with self._lock:
            if self._voice is None:
                if not self.model_path.is_file():
                    raise LocalVoiceError(
                        f"No se encontró el modelo Piper: {self.model_path}"
                    )
                try:
                    from piper import PiperVoice
                except ImportError as exc:
                    raise LocalVoiceError(
                        "piper-tts no está instalado; usa la dependencia voice."
                    ) from exc
                self._voice = PiperVoice.load(
                    str(self.model_path), use_cuda=self.use_cuda
                )
        return self._voice

    def _synthesize_sync(self, text: str) -> bytes:
        with self._inference_lock:
            output = io.BytesIO()
            with wave.open(output, "wb") as wav_file:
                self._get_voice().synthesize_wav(text, wav_file)
            return output.getvalue()

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        del voice  # Un archivo ONNX representa una voz concreta.
        if not text.strip():
            return b""
        return await asyncio.to_thread(self._synthesize_sync, text)

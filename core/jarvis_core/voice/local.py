"""Motores locales de voz con carga diferida para no ralentizar el gateway."""

from __future__ import annotations

import asyncio
import io
import threading
import wave
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


def _pcm16_wav(samples: Any, sample_rate: int) -> bytes:
    """Empaqueta muestras float (-1..1) como WAV PCM 16 bits mono."""
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return b""
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output.getvalue()


class KokoroTTS:
    """Síntesis local con Kokoro-82M, natural y suficientemente rápida en CPU.

    Kokoro devuelve audio float32 a 24 kHz; aquí se convierte a WAV PCM16 para
    mantener el mismo contrato que el resto de motores.

    Requiere `espeak-ng` instalado en el sistema (Kokoro lo usa como fonemizador
    para español).
    """

    SAMPLE_RATE = 24000

    def __init__(
        self,
        voice: str = "em_alex",
        *,
        lang_code: str = "e",
        speed: float = 1.0,
        repo_id: str | None = None,
    ) -> None:
        self.voice = voice
        self.lang_code = lang_code
        self.speed = speed
        self.repo_id = repo_id
        self._pipeline: Any = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is None:
                try:
                    from kokoro import KPipeline
                except ImportError as exc:
                    raise LocalVoiceError(
                        "kokoro no está instalado; usa la dependencia voice."
                    ) from exc
                kwargs: dict[str, Any] = {"lang_code": self.lang_code}
                if self.repo_id:
                    kwargs["repo_id"] = self.repo_id
                try:
                    self._pipeline = KPipeline(**kwargs)
                except Exception as exc:
                    raise LocalVoiceError(
                        f"No se pudo inicializar Kokoro ({exc}). "
                        "Comprueba que 'espeak-ng' esté instalado."
                    ) from exc
        return self._pipeline

    @staticmethod
    def _extract_audio(chunk: Any) -> Any:
        """Obtiene el audio de un fragmento, tolerando tuplas u objetos Result."""
        audio = chunk
        if isinstance(chunk, tuple):
            audio = chunk[-1]
        elif hasattr(chunk, "audio"):
            audio = chunk.audio
        # Kokoro puede devolver tensores de torch.
        if hasattr(audio, "detach"):
            audio = audio.detach()
        if hasattr(audio, "cpu"):
            audio = audio.cpu()
        if hasattr(audio, "numpy"):
            audio = audio.numpy()
        return audio

    def _synthesize_sync(self, text: str) -> bytes:
        import numpy as np

        with self._inference_lock:
            pipeline = self._get_pipeline()
            try:
                chunks = [
                    self._extract_audio(chunk)
                    for chunk in pipeline(text, voice=self.voice, speed=self.speed)
                ]
            except Exception as exc:
                raise LocalVoiceError(f"Kokoro falló al sintetizar: {exc}") from exc

            usable = [np.asarray(c, dtype=np.float32).reshape(-1) for c in chunks]
            usable = [c for c in usable if c.size]
            if not usable:
                return b""
            return _pcm16_wav(np.concatenate(usable), self.SAMPLE_RATE)

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        del voice  # La voz se fija al construir el motor.
        if not text.strip():
            return b""
        return await asyncio.to_thread(self._synthesize_sync, text)

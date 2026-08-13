"""Tests del motor Kokoro y su conversión de audio a WAV.

No descargan modelos: inyectan un doble del pipeline de Kokoro.
"""

import io
import math
import wave

import numpy as np
import pytest
from jarvis_core.config import Settings
from jarvis_core.voice.factory import build_tts
from jarvis_core.voice.local import KokoroTTS, LocalVoiceError


class FakeKokoroPipeline:
    """Imita KPipeline: genera tuplas (graphemes, phonemes, audio float32)."""

    def __init__(self, chunks: int = 2, samples: int = 2400) -> None:
        self.chunks = chunks
        self.samples = samples
        self.calls: list[dict] = []

    def __call__(self, text, voice=None, speed=1.0):
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        for _ in range(self.chunks):
            yield (
                "g",
                "p",
                np.array(
                    [
                        0.5 * math.sin(2 * math.pi * 440 * i / 24000)
                        for i in range(self.samples)
                    ],
                    dtype="float32",
                ),
            )


# ── Construcción del motor ───────────────────────────────────────────────────


def test_default_tts_is_kokoro_with_alex_voice():
    tts = build_tts(Settings())
    assert isinstance(tts, KokoroTTS)
    assert tts.voice == "em_alex"
    assert tts.lang_code == "e"


# ── Conversión de audio ──────────────────────────────────────────────────────


async def test_kokoro_produces_valid_wav():
    tts = KokoroTTS()
    tts._pipeline = FakeKokoroPipeline(chunks=2, samples=2400)

    data = await tts.synthesize("Hola, soy JARVIS.")

    assert data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2  # PCM 16 bits
        assert wav_file.getframerate() == KokoroTTS.SAMPLE_RATE == 24000
        assert wav_file.getnframes() == 4800  # los dos fragmentos concatenados


async def test_kokoro_passes_voice_and_speed():
    fake = FakeKokoroPipeline()
    tts = KokoroTTS(voice="em_alex", speed=1.2)
    tts._pipeline = fake

    await tts.synthesize("Prueba")

    assert fake.calls[0]["voice"] == "em_alex"
    assert fake.calls[0]["speed"] == 1.2


async def test_kokoro_empty_text_returns_no_audio():
    tts = KokoroTTS()
    tts._pipeline = FakeKokoroPipeline()
    assert await tts.synthesize("   ") == b""


async def test_kokoro_wraps_pipeline_errors():
    class Failing:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("voz inexistente")
            yield  # pragma: no cover

    tts = KokoroTTS()
    tts._pipeline = Failing()
    with pytest.raises(LocalVoiceError):
        await tts.synthesize("Hola")


# ── Tolerancia a los formatos que devuelve Kokoro ────────────────────────────


def test_extract_audio_handles_tuple_result_and_tensor():
    arr = np.array([0.1, 0.2], dtype="float32")

    class Result:
        def __init__(self, audio):
            self.audio = audio

    class FakeTensor:
        def __init__(self, audio):
            self._audio = audio

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self._audio

    assert np.allclose(KokoroTTS._extract_audio(("g", "p", arr)), arr)
    assert np.allclose(KokoroTTS._extract_audio(Result(arr)), arr)
    assert np.allclose(KokoroTTS._extract_audio(("g", "p", FakeTensor(arr))), arr)
    assert np.allclose(KokoroTTS._extract_audio(arr), arr)

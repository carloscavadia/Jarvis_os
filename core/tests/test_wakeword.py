"""Tests del detector de palabra de activación.

No cargan openWakeWord: inyectan un doble del modelo para poder comprobar el
troceado en frames, el umbral y el periodo refractario de forma determinista.
"""

import numpy as np
import pytest
from jarvis_core.voice.local import LocalVoiceError
from jarvis_core.voice.wakeword import WakeWordDetector

FRAME = WakeWordDetector.FRAME_BYTES


class FakeModel:
    """Imita openwakeword.Model.predict devolviendo puntuaciones programadas."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.frames: list[np.ndarray] = []
        self.resets = 0

    def predict(self, samples):
        self.frames.append(samples)
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def build(scores: list[float], **kwargs) -> tuple[WakeWordDetector, FakeModel]:
    detector = WakeWordDetector(**kwargs)
    fake = FakeModel(scores)
    detector._model = fake
    return detector, fake


def silence(frames: int = 1) -> bytes:
    return b"\x00\x00" * (WakeWordDetector.FRAME_SAMPLES * frames)


# ── Troceado en frames ───────────────────────────────────────────────────────


def test_incomplete_chunk_is_buffered_until_a_full_frame_arrives():
    detector, fake = build([0.0])
    assert detector.process(silence()[: FRAME // 2]) is None
    assert fake.frames == []  # aún no hay 80 ms completos

    detector.process(silence()[FRAME // 2 :])
    assert len(fake.frames) == 1
    assert fake.frames[0].shape == (WakeWordDetector.FRAME_SAMPLES,)


def test_oversized_chunk_is_split_into_frames():
    detector, fake = build([0.0, 0.0, 0.0])
    detector.process(silence(3))
    assert len(fake.frames) == 3


def test_samples_are_decoded_as_signed_16_bit():
    detector, fake = build([0.0])
    expected = np.arange(-600, 680, dtype="<i2")
    detector.process(expected.tobytes())
    assert np.array_equal(fake.frames[0], expected)


def test_buffer_never_grows_without_bound():
    detector, _ = build([])
    # Un cliente averiado que manda audio y nunca completa un frame.
    detector.process(b"\x00" * (WakeWordDetector.MAX_BUFFER_BYTES * 3 + 1))
    assert len(detector._buffer) <= WakeWordDetector.MAX_BUFFER_BYTES


# ── Umbral ───────────────────────────────────────────────────────────────────


def test_score_below_threshold_does_not_activate():
    detector, _ = build([0.49], threshold=0.5)
    assert detector.process(silence()) is None


def test_score_above_threshold_activates_and_reports_it():
    detector, _ = build([0.87], threshold=0.5)
    assert detector.process(silence()) == pytest.approx(0.87)


def test_threshold_is_configurable():
    detector, _ = build([0.3], threshold=0.2)
    assert detector.process(silence()) == pytest.approx(0.3)


# ── Periodo refractario ──────────────────────────────────────────────────────


def test_one_phrase_activates_once_even_if_several_frames_score_high():
    # openWakeWord mantiene la puntuación alta durante varios frames seguidos.
    detector, _ = build([0.9, 0.95, 0.92, 0.9], refractory_seconds=2.0)
    assert detector.process(silence(4)) == pytest.approx(0.9)

    # Los frames restantes del mismo fragmento ya no vuelven a disparar.
    assert detector.process(silence(4)) is None


def test_a_second_phrase_activates_once_pasado_el_refractario():
    detector, fake = build([0.9] + [0.0] * 25 + [0.88], refractory_seconds=1.0)
    assert detector.process(silence()) == pytest.approx(0.9)
    # 1 s son 12,5 frames de 80 ms; tras 25 frames de silencio vuelve a admitir.
    assert detector.process(silence(25)) is None
    assert detector.process(silence()) == pytest.approx(0.88)
    assert fake.resets >= 2


def test_activation_clears_pending_audio():
    detector, fake = build([0.9, 0.9])
    detector.process(silence(2))
    # Tras activar se descarta lo que quedaba: el segundo frame no llega a predict.
    assert len(fake.frames) == 1
    assert fake.resets == 1
    assert detector._buffer == b""


# ── Errores ──────────────────────────────────────────────────────────────────


def test_missing_dependency_raises_a_clear_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def explode(name, *args, **kwargs):
        if name.startswith("openwakeword"):
            raise ImportError("no module named openwakeword")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", explode)
    with pytest.raises(LocalVoiceError, match="openwakeword"):
        WakeWordDetector().process(silence())


def test_model_load_failure_is_wrapped(monkeypatch):
    detector = WakeWordDetector("modelo_inexistente")
    monkeypatch.setattr(
        "openwakeword.utils.download_models",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("modelo desconocido")),
    )
    with pytest.raises(LocalVoiceError, match="modelo_inexistente"):
        detector.process(silence())

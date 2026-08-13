"""Detección local de la palabra de activación «Hey JARVIS».

Usa openWakeWord, que trae un modelo preentrenado para esa frase exacta. El audio
nunca sale del servidor: no hay reconocimiento de voz en la nube en la etapa que
está permanentemente escuchando.
"""

from __future__ import annotations

import logging

from jarvis_core.voice.local import LocalVoiceError

logger = logging.getLogger("jarvis.wakeword")


class WakeWordDetector:
    """Consume PCM de 16 kHz y avisa cuando oye la frase de activación.

    El cliente envía fragmentos de tamaño arbitrario, así que el detector conserva
    el resto entre llamadas y solo evalúa bloques completos de 80 ms, que es la
    ventana con la que se entrenó el modelo.
    """

    SAMPLE_RATE = 16000
    FRAME_SAMPLES = 1280  # 80 ms
    FRAME_BYTES = FRAME_SAMPLES * 2  # PCM de 16 bits, mono
    # Un fragmento suelto nunca debería superar unos pocos frames; el tope evita
    # que un cliente averiado haga crecer la memoria sin límite.
    MAX_BUFFER_BYTES = FRAME_BYTES * 64

    def __init__(
        self,
        model: str = "hey_jarvis",
        *,
        threshold: float = 0.5,
        vad_threshold: float = 0.0,
        refractory_seconds: float = 2.0,
        inference_framework: str = "onnx",
    ) -> None:
        self.model = model
        self.threshold = threshold
        self.vad_threshold = vad_threshold
        self.refractory_seconds = max(0.0, refractory_seconds)
        self.inference_framework = inference_framework
        self._model = None
        self._buffer = bytearray()
        # Segundos de audio procesados desde la última activación. Se usa en lugar
        # del reloj real para que el detector sea determinista en las pruebas y no
        # dependa de la velocidad a la que llegue el audio.
        self._since_detection = refractory_seconds

    # ── Carga perezosa ───────────────────────────────────────────────────────

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise LocalVoiceError(
                "Falta openwakeword. Instala el extra:  pip install './core[voice]'."
            ) from exc
        try:
            download_models(model_names=[self.model])
            self._model = Model(
                wakeword_models=[self.model],
                vad_threshold=self.vad_threshold,
                inference_framework=self.inference_framework,
            )
        except Exception as exc:
            raise LocalVoiceError(
                f"No pude cargar el modelo de activación '{self.model}': {exc}"
            ) from exc
        logger.info("Modelo de activación cargado: %s", self.model)
        return self._model

    def warm_up(self) -> None:
        """Carga el modelo por adelantado para que la primera activación no tarde."""
        self._ensure_model()

    # ── Detección ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Olvida el audio acumulado. Se llama tras activar y al reanudar escucha."""
        self._buffer.clear()
        if self._model is not None:
            self._model.reset()

    def scores(self, pcm: bytes) -> list[float]:
        """Puntúa un audio completo sin umbral ni refractario. Para calibrar.

        Devuelve la puntuación de cada ventana de 80 ms, de modo que se pueda ver
        la curva entera y elegir el umbral con conocimiento de causa.
        """
        import numpy as np

        model = self._ensure_model()
        frames = len(pcm) // self.FRAME_BYTES
        return [
            float(
                model.predict(
                    np.frombuffer(
                        pcm[index * self.FRAME_BYTES : (index + 1) * self.FRAME_BYTES],
                        dtype="<i2",
                    )
                ).get(self.model, 0.0)
            )
            for index in range(frames)
        ]

    def process(self, pcm: bytes) -> float | None:
        """Devuelve la puntuación si el fragmento dispara la activación, o None.

        `pcm` es audio mono PCM de 16 bits con signo a 16 kHz, tal cual lo manda el
        cliente. La llamada es síncrona y usa CPU: invócala en un hilo aparte.

        La puntuación devuelta es la del frame que **cruzó** el umbral, no el pico
        de la frase: se activa en cuanto hay certeza suficiente para no perder
        tiempo de respuesta. Para ver la curva completa usa `scores()`.
        """
        import numpy as np

        model = self._ensure_model()
        self._buffer.extend(pcm)
        if len(self._buffer) > self.MAX_BUFFER_BYTES:
            # Descarta lo más antiguo: perder audio viejo es preferible a crecer.
            del self._buffer[: len(self._buffer) - self.MAX_BUFFER_BYTES]

        detected: float | None = None
        while len(self._buffer) >= self.FRAME_BYTES:
            frame = bytes(self._buffer[: self.FRAME_BYTES])
            del self._buffer[: self.FRAME_BYTES]
            samples = np.frombuffer(frame, dtype="<i2")
            scores = model.predict(samples)
            score = float(scores.get(self.model, 0.0))
            self._since_detection += self.FRAME_SAMPLES / self.SAMPLE_RATE
            if score < self.threshold or self._since_detection < self.refractory_seconds:
                continue
            # Una sola frase mantiene la puntuación alta durante varios frames; se
            # informa la primera y se reinicia para no encadenar activaciones.
            detected = score
            self._since_detection = 0.0
            self.reset()
            break
        return detected


def read_wav_as_pcm16k(path: str) -> bytes:
    """Lee un WAV cualquiera y lo deja en el formato que espera el detector.

    Acepta estéreo y cualquier frecuencia; convierte a mono de 16 kHz. Se hace con
    numpy a propósito: `audioop`, la vía obvia, desaparece en Python 3.13.
    """
    import wave

    import numpy as np

    with wave.open(path, "rb") as audio:
        if audio.getsampwidth() != 2:
            raise LocalVoiceError(f"{path}: se requiere PCM de 16 bits.")
        channels = audio.getnchannels()
        rate = audio.getframerate()
        samples = np.frombuffer(
            audio.readframes(audio.getnframes()), dtype="<i2"
        ).astype(np.float32)

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != WakeWordDetector.SAMPLE_RATE:
        target = round(len(samples) * WakeWordDetector.SAMPLE_RATE / rate)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, target),
            np.arange(len(samples)),
            samples,
        )
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def calibrate(paths: list[str], model: str = "hey_jarvis") -> list[tuple[str, float]]:
    """Devuelve el pico de puntuación de cada WAV, para elegir el umbral."""
    results: list[tuple[str, float]] = []
    detector = WakeWordDetector(model)
    # Un segundo de silencio delante llena el buffer interno del modelo, igual que
    # ocurre con el flujo continuo de un micrófono real.
    pad = b"\x00\x00" * WakeWordDetector.SAMPLE_RATE
    for path in paths:
        detector.reset()
        results.append((path, max(detector.scores(pad + read_wav_as_pcm16k(path)), default=0.0)))
    return results

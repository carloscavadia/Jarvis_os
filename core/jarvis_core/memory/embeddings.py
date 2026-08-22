"""Vectores de significado para la memoria a largo plazo.

Hasta ahora la memoria se buscaba por coincidencia de texto: si guardaste «mi
coche es un Tesla Model 3 azul» y preguntabas «¿cuánto tarda en cargar el
vehículo?», no encontraba nada, porque no comparten ni una palabra. Un
embedding convierte cada hecho en un vector, y hechos que significan cosas
parecidas quedan cerca aunque no compartan vocabulario.

El modelo corre **en el servidor**, como Whisper y Kokoro: lo que recuerda
JARVIS de su jefe no sale de la red. Y es opcional de principio a fin — sin él
la memoria sigue funcionando por texto y recencia, que es lo que había.
"""

from __future__ import annotations

import logging
import math
import struct
from typing import Protocol

logger = logging.getLogger("jarvis.memory.embeddings")

#: Modelo por defecto: multilingüe y pequeño. El español importa aquí, y los
#: modelos entrenados solo en inglés fallan justo con lo que se le va a pedir.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    """Convierte texto en un vector de significado."""

    def embed(self, text: str) -> list[float]: ...


def pack_vector(vector: list[float]) -> bytes:
    """Empaqueta el vector para guardarlo en SQLite sin dependencias extra."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cercanía entre dos significados, de -1 a 1."""
    if not a or not b or len(a) != len(b):
        return 0.0
    producto = sum(x * y for x, y in zip(a, b, strict=True))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if not norma_a or not norma_b:
        return 0.0
    return producto / (norma_a * norma_b)


class LocalEmbedder:
    """`sentence-transformers` cargado bajo demanda y en el propio servidor.

    La carga es perezosa a propósito: el modelo pesa cientos de megas y se
    descarga la primera vez. Hacerlo al arrancar retrasaría el gateway entero
    por una función que quizá no se use en toda la sesión.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, cache_dir: str = "") -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._failed = False

    def _load(self):
        if self._model is not None or self._failed:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.info(
                "sentence-transformers no está instalado; la memoria seguirá "
                "buscando por texto. Instálalo con: pip install 'jarvis-core[memory]'"
            )
            self._failed = True
            return None
        try:
            kwargs = {"cache_folder": self.cache_dir} if self.cache_dir else {}
            self._model = SentenceTransformer(self.model_name, **kwargs)
            logger.info("Modelo de embeddings listo: %s", self.model_name)
        except Exception:
            # Sin red en el primer arranque, o modelo inexistente. No es motivo
            # para tumbar la memoria: se degrada a búsqueda por texto.
            logger.warning(
                "No pude cargar el modelo de embeddings %s; la memoria buscará "
                "por texto.", self.model_name, exc_info=True
            )
            self._failed = True
        return self._model

    @property
    def available(self) -> bool:
        return self._load() is not None

    def embed(self, text: str) -> list[float]:
        model = self._load()
        if model is None or not text.strip():
            return []
        try:
            return [float(x) for x in model.encode(text, normalize_embeddings=True)]
        except Exception:
            logger.warning("Falló el cálculo del embedding", exc_info=True)
            return []


class OpenAICompatibleEmbedder:
    """Embeddings por el mismo endpoint compatible con OpenAI que ya usas.

    NVIDIA NIM sirve modelos de embedding en `/v1/embeddings`, así que si ya
    tienes ahí tu `JARVIS_OPENAI_BASE_URL` y tu clave, esto no descarga nada ni
    gasta CPU del servidor: reutiliza lo que hay.

    A cambio, los hechos que recuerda JARVIS de su jefe salen de la red para
    convertirse en vectores. Por eso no es el modo por defecto.
    """

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = None
        self._failed = False

    def _load(self):
        if self._client is not None or self._failed:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            logger.info("El SDK de OpenAI no está instalado; sin embeddings remotos.")
            self._failed = True
            return None
        if not self._api_key or not self._base_url:
            self._failed = True
            return None
        self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    @property
    def available(self) -> bool:
        return self._load() is not None

    def embed(self, text: str) -> list[float]:
        client = self._load()
        if client is None or not text.strip():
            return []
        try:
            respuesta = client.embeddings.create(model=self.model, input=text)
            return [float(x) for x in respuesta.data[0].embedding]
        except Exception:
            logger.warning("Falló el embedding remoto", exc_info=True)
            return []


def build_embedder(settings) -> Embedder | None:
    """Elige el embedder según la configuración, o ninguno.

    El orden por defecto pone lo local primero: lo que JARVIS sabe de su jefe es
    exactamente lo que no conviene mandar fuera, y el modelo corre sobre el
    torch que Kokoro ya trae a la imagen. Si no está disponible y hay un
    endpoint compatible configurado, se usa ese. Y si no hay ninguno, la memoria
    sigue buscando por texto como hasta ahora.
    """
    modo = (settings.embeddings_provider or "auto").lower()
    if modo in {"off", "none", "disabled"}:
        return None

    local = LocalEmbedder(settings.embeddings_model, settings.stt_download_root)
    remoto = OpenAICompatibleEmbedder(
        settings.openai_api_key,
        settings.openai_base_url,
        settings.embeddings_remote_model,
    )

    if modo == "local":
        return local if local.available else None
    if modo in {"openai", "nvidia", "remote"}:
        return remoto if remoto.available else None

    if local.available:
        return local
    if remoto.available:
        logger.info("Sin modelo local: los embeddings irán por %s", settings.openai_base_url)
        return remoto
    return None

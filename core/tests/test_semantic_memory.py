"""Memoria que busca por significado, no por coincidencia de texto.

El caso que no funcionaba: guardas «mi coche es un Tesla Model 3 azul» y
preguntas «¿cuánto tarda en cargar el vehículo?». No comparten ni una palabra,
así que la búsqueda por texto no devolvía nada y JARVIS respondía como si no
supiera nada de tu coche.

El embedder de estos tests es un doble determinista: lo que se prueba es el
almacén y la degradación, no la calidad de un modelo de terceros.
"""

import math

from jarvis_core.config import Settings
from jarvis_core.memory.embeddings import (
    build_embedder,
    cosine_similarity,
    pack_vector,
    unpack_vector,
)
from jarvis_core.memory.store import MemoryStore

#: Un espacio de significado de juguete: cada concepto es un eje.
CONCEPTOS = {
    "coche": [1.0, 0.0, 0.0],
    "vehiculo": [0.95, 0.1, 0.0],
    "tesla": [0.9, 0.0, 0.1],
    "cargar": [0.8, 0.2, 0.0],
    "cafe": [0.0, 1.0, 0.0],
    "desayuno": [0.05, 0.95, 0.0],
    "casa": [0.0, 0.0, 1.0],
}


class EmbedderFalso:
    """Suma los ejes de los conceptos que aparecen en el texto."""

    def __init__(self) -> None:
        self.llamadas = 0

    def embed(self, text: str) -> list[float]:
        self.llamadas += 1
        vector = [0.0, 0.0, 0.0]
        minuscula = text.lower()
        for concepto, eje in CONCEPTOS.items():
            if concepto in minuscula:
                vector = [a + b for a, b in zip(vector, eje, strict=True)]
        norma = math.sqrt(sum(x * x for x in vector))
        return [x / norma for x in vector] if norma else []


def test_el_vector_sobrevive_al_viaje_por_sqlite():
    vector = [0.125, -0.5, 0.875]
    assert [round(x, 4) for x in unpack_vector(pack_vector(vector))] == vector


def test_el_coseno_mide_lo_que_debe():
    assert round(cosine_similarity(CONCEPTOS["coche"], CONCEPTOS["coche"]), 3) == 1.0
    assert cosine_similarity(CONCEPTOS["coche"], CONCEPTOS["vehiculo"]) > 0.9
    assert cosine_similarity(CONCEPTOS["coche"], CONCEPTOS["cafe"]) < 0.2
    # Aristas: vectores vacíos o de distinto tamaño no revientan.
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_encuentra_por_significado_lo_que_el_texto_no_encontraba(tmp_path):
    memoria = MemoryStore(str(tmp_path / "m.db"), embedder=EmbedderFalso())
    memoria.remember("coche", "un Tesla Model 3 azul")
    memoria.remember("cafe", "solo, sin azúcar")

    # Ni «vehiculo» ni «cargar» aparecen en el hecho guardado.
    assert memoria.recall("vehiculo") == [], "el test perdería su sentido"
    encontrados = [m.key for m in memoria.recall_semantic("cargar el vehiculo")]

    assert encontrados[0] == "coche"
    assert "cafe" not in encontrados


def test_no_devuelve_lo_que_no_se_parece(tmp_path):
    memoria = MemoryStore(str(tmp_path / "m.db"), embedder=EmbedderFalso())
    memoria.remember("casa", "en la sierra")

    assert memoria.recall_semantic("cafe", min_similarity=0.5) == []


def test_sin_embedder_la_memoria_sigue_funcionando(tmp_path):
    """Lo importante: no tener el modelo nunca puede romper la memoria."""
    memoria = MemoryStore(str(tmp_path / "m.db"))
    memoria.remember("coche", "un Tesla Model 3 azul")

    assert [m.key for m in memoria.recall_semantic("coche")] == ["coche"]


def test_un_embedder_averiado_cae_a_la_busqueda_por_texto(tmp_path):
    class Averiado:
        def embed(self, text):
            return []

    memoria = MemoryStore(str(tmp_path / "m.db"), embedder=Averiado())
    memoria.remember("coche", "un Tesla")

    assert [m.key for m in memoria.recall_semantic("coche")] == ["coche"]


def test_los_hechos_guardados_antes_reciben_su_vector(tmp_path):
    """Activar la memoria semántica no puede exigir volver a enseñárselo todo."""
    ruta = str(tmp_path / "m.db")
    antigua = MemoryStore(ruta)
    antigua.remember("coche", "un Tesla Model 3 azul")
    antigua.close()

    nueva = MemoryStore(ruta, embedder=EmbedderFalso())
    encontrados = [m.key for m in nueva.recall_semantic("cargar el vehiculo")]

    assert encontrados == ["coche"], "el hecho antiguo se quedó sin vector"


def test_la_base_antigua_se_migra_sin_perder_nada(tmp_path):
    ruta = str(tmp_path / "m.db")
    antigua = MemoryStore(ruta)
    antigua.remember("cafe", "solo, sin azúcar")
    antigua.close()

    nueva = MemoryStore(ruta, embedder=EmbedderFalso())

    assert [m.value for m in nueva.recall("cafe")] == ["solo, sin azúcar"]


def test_apagar_los_embeddings_deja_la_memoria_como_estaba():
    assert build_embedder(Settings(embeddings_provider="off")) is None


def test_el_modo_local_no_se_va_a_la_red_aunque_haya_endpoint():
    """Lo que JARVIS sabe de su jefe es justo lo que no conviene mandar fuera."""
    elegido = build_embedder(
        Settings(
            embeddings_provider="local",
            openai_api_key="nvapi-loquesea",
            openai_base_url="https://integrate.api.nvidia.com/v1",
        )
    )
    # Sin sentence-transformers instalado, `local` devuelve None en vez de
    # recurrir en silencio al endpoint remoto.
    assert elegido is None or type(elegido).__name__ == "LocalEmbedder"

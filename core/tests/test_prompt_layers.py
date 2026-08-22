"""Las dos capas del prompt: la que se cachea y la que cambia cada turno.

El prompt de sistema y las definiciones de herramientas se repiten palabra por
palabra en cada vuelta del bucle de herramientas —hasta 12 por turno— y se
pagaban enteras cada vez, porque no había caché. Meter ahí lo que cambia (la
fecha, los hechos recordados, las reglas de la casa vigentes) habría anulado el
ahorro: cualquier byte distinto en el prefijo invalida todo lo que va detrás.

De ahí la separación: `system` es lo estable y cacheable, `system_overlay` lo
volátil, y va en un mensaje de sistema a mitad de conversación.
"""

import json

from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMResponse
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tools.base import ToolRegistry


class ProveedorEspia:
    name = "espia"

    def __init__(self) -> None:
        self.llamadas: list[dict] = []

    async def complete(self, *, system, history, tools, on_text_delta=None, system_overlay=""):
        self.llamadas.append({"system": system, "overlay": system_overlay})
        return LLMResponse(text="Hecho.", stop_reason="end_turn", provider=self.name)


def _agente(tmp_path, memoria=None, **ajustes):
    settings = Settings(memory_db_path=str(tmp_path / "m.db"), **ajustes)
    proveedor = ProveedorEspia()
    return proveedor, Orchestrator(
        proveedor, ToolRegistry(), settings, memory=memoria
    )


async def test_el_prompt_estable_no_cambia_entre_turnos(tmp_path):
    """Si cambiara, la caché se invalidaría en cada turno y el ahorro sería cero."""
    proveedor, agente = _agente(tmp_path)

    await agente.send("hola")
    await agente.send("¿qué tal?")

    assert proveedor.llamadas[0]["system"] == proveedor.llamadas[1]["system"]


async def test_la_fecha_va_en_la_capa_volatil_no_en_el_prompt(tmp_path):
    """Una fecha dentro del prompt estable es el invalidador silencioso clásico."""
    proveedor, agente = _agente(tmp_path, timezone="Europe/Madrid")

    await agente.send("hola")

    llamada = proveedor.llamadas[0]
    assert "Fecha y hora actuales" in llamada["overlay"]
    assert "Fecha y hora actuales" not in llamada["system"]


async def test_los_hechos_recordados_llegan_sin_que_haya_que_pedirlos(tmp_path):
    """La memoria solo existía como herramienta: si no llamaba a `recall`, nada."""
    memoria = MemoryStore(str(tmp_path / "m.db"))
    memoria.remember("coche", "un Tesla Model 3 azul")
    memoria.remember("cafe", "solo, sin azúcar")
    proveedor, agente = _agente(tmp_path, memoria=memoria)

    await agente.send("¿cómo tengo el coche?")

    overlay = proveedor.llamadas[0]["overlay"]
    assert "Tesla Model 3 azul" in overlay
    assert "memoria a largo plazo" in overlay
    # Y tampoco en el prompt estable: cada hecho nuevo romperia la cache.
    assert "Tesla" not in proveedor.llamadas[0]["system"]


async def test_se_respeta_el_tope_de_hechos(tmp_path):
    memoria = MemoryStore(str(tmp_path / "m.db"))
    for i in range(40):
        memoria.remember(f"dato_{i}", f"valor {i}")
    proveedor, agente = _agente(tmp_path, memoria=memoria, memory_facts_in_prompt=5)

    await agente.send("cuéntame")

    assert proveedor.llamadas[0]["overlay"].count("\n- ") <= 5


async def test_con_cero_hechos_la_memoria_no_se_inyecta(tmp_path):
    """Vuelta al comportamiento anterior para quien lo prefiera."""
    memoria = MemoryStore(str(tmp_path / "m.db"))
    memoria.remember("coche", "un Tesla")
    proveedor, agente = _agente(tmp_path, memoria=memoria, memory_facts_in_prompt=0)

    await agente.send("hola")

    assert "Tesla" not in proveedor.llamadas[0]["overlay"]


async def test_la_personalidad_se_cambia_en_caliente(tmp_path):
    """Antes exigía reiniciar el gateway: el prompt se congela al construirlo."""
    proveedor, agente = _agente(tmp_path)
    await agente.send("hola")
    assert "Trátame de usted" not in proveedor.llamadas[0]["overlay"]

    agente.set_persona("Trátame de usted. Nada de emojis.")
    await agente.send("hola otra vez")

    overlay = proveedor.llamadas[1]["overlay"]
    assert "Trátame de usted" in overlay
    assert "Reglas de la casa vigentes" in overlay
    # Lo importante: el prefijo cacheado sigue siendo el mismo.
    assert proveedor.llamadas[0]["system"] == proveedor.llamadas[1]["system"]


async def test_una_memoria_rota_no_tumba_el_turno(tmp_path):
    class MemoriaRota:
        def recall(self, *args, **kwargs):
            raise RuntimeError("base de datos bloqueada")

    proveedor, agente = _agente(tmp_path, memoria=MemoriaRota())

    respuesta = await agente.send("hola")

    assert respuesta.text == "Hecho."

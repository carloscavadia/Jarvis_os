"""El turno del agente siempre termina, pase lo que pase con el proveedor.

El gateway solo manda su mensaje final cuando el bucle de agente retorna. Si no
retorna, el HUD se queda sin cierre de conversación, sin teclado y sin voz —la
síntesis también sale al cerrar el turno—, y sin ninguna forma de recuperarse:
había que recargar la página. Ninguna llamada al proveedor tenía tope.

El segundo test cubre lo que hacía probable ese cuelgue: el resultado íntegro de
una herramienta volvía al modelo y se quedaba en el historial, así que viajaba
de nuevo en cada vuelta del bucle. Con las 148 entidades de Home Assistant, cada
iteración salía más lenta que la anterior.
"""

import asyncio
import json

from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMResponse, ToolCall
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult


class ProveedorColgado:
    """Acepta la petición y no responde jamás. El caso real de un proveedor caído."""

    def __init__(self) -> None:
        self.llamadas = 0

    async def complete(self, **kwargs):
        self.llamadas += 1
        await asyncio.Event().wait()


class InventarioTool(Tool):
    name = "listar_entidades"
    description = "Devuelve el inventario de la casa."
    input_schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs) -> ToolResult:
        entidades = [
            {
                "entity_id": f"sensor.backup_last_successful_automatic_backup_{i}",
                "name": f"Backup Last successful automatic backup {i}",
                "domain": "sensor",
                "state": "unknown",
            }
            for i in range(148)
        ]
        return ToolResult(
            json.dumps({"total": 148, "entities": entidades}, separators=(",", ":"))
        )


class ProveedorQuePideLaHerramienta:
    """Pide la herramienta una vez y luego responde. Guarda lo que se le devolvió."""

    def __init__(self) -> None:
        self.historial_visto: list = []
        self.overlay_visto = ""

    async def complete(self, *, system, history, tools, on_text_delta=None, system_overlay=""):
        self.historial_visto = [dict(item) for item in history]
        self.overlay_visto = system_overlay
        pidio_ya = any(item.get("role") == "tool" for item in history)
        if pidio_ya:
            return LLMResponse(
                text="Tienes 148 entidades, todas sensores de copia de seguridad.",
                tool_calls=[],
                stop_reason="end_turn",
                provider="fake",
                assistant_content=None,
            )
        return LLMResponse(
            text="",
            tool_calls=[ToolCall(id="t1", name="listar_entidades", input={})],
            stop_reason="tool_use",
            provider="fake",
            assistant_content=None,
        )


def _orquestador(proveedor, tmp_path, **ajustes):
    settings = Settings(memory_db_path=str(tmp_path / "m.db"), **ajustes)
    registry = ToolRegistry()
    registry.register(InventarioTool())
    return Orchestrator(proveedor, registry, settings)


async def test_un_proveedor_que_no_responde_no_cuelga_el_turno(tmp_path):
    proveedor = ProveedorColgado()
    agente = _orquestador(proveedor, tmp_path, llm_timeout_seconds=10.0)

    # El tope real es de minutos; aquí se comprueba que existe y que corta.
    respuesta = await asyncio.wait_for(
        _con_reloj_acelerado(agente.send("hola")), timeout=5
    )

    assert respuesta.stop_reason == "timeout"
    assert "no respondió a tiempo" in respuesta.text
    assert proveedor.llamadas == 1


async def _con_reloj_acelerado(coro):
    """Ejecuta la corrutina con un `wait_for` que vence de inmediato."""
    original = asyncio.wait_for

    async def rapido(awaitable, timeout):
        return await original(awaitable, 0.05 if timeout and timeout > 1 else timeout)

    asyncio.wait_for = rapido
    try:
        return await coro
    finally:
        asyncio.wait_for = original


async def test_al_modelo_se_le_devuelve_el_resultado_recortado(tmp_path):
    proveedor = ProveedorQuePideLaHerramienta()
    agente = _orquestador(proveedor, tmp_path, max_tool_output_chars=2000)

    respuesta = await agente.send("lista mis dispositivos")

    assert respuesta.text.startswith("Tienes 148 entidades")
    resultado = next(
        item for item in proveedor.historial_visto if item.get("role") == "tool"
    )
    contenido = resultado["results"][0]["content"]

    cuerpo, _, aviso = contenido.partition("\n\n[Resultado recortado:")
    assert len(cuerpo) <= 2000, "el resultado entero volvió al modelo"
    # Recortado pero íntegro: el modelo tiene que poder leerlo.
    datos = json.loads(cuerpo)
    assert 0 < len(datos["entities"]) < 148
    assert datos["total"] == 148
    # Y tiene que saber que hay más, o afirmará que eso es todo lo que existe.
    assert "de 148" in aviso and "filtro" in aviso


async def test_un_resultado_pequeno_llega_intacto_y_sin_avisos(tmp_path):
    proveedor = ProveedorQuePideLaHerramienta()
    agente = _orquestador(proveedor, tmp_path, max_tool_output_chars=100_000)

    await agente.send("lista mis dispositivos")

    resultado = next(
        item for item in proveedor.historial_visto if item.get("role") == "tool"
    )
    assert "[Resultado recortado" not in resultado["results"][0]["content"]

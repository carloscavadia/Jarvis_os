"""Subagentes especializados: aislamiento, aprobaciones y límites.

Un subagente no es «el mismo agente con otro prompt». Lo que lo hace útil es que
**ve menos**: el de casa no sabe que existe el workspace, el de investigación no
puede tocar tus archivos. Y lo que lo hace seguro es que ese «ver menos» no
debilita nada de lo que ya protegía al agente principal.
"""

import asyncio
from typing import ClassVar

import pytest
from jarvis_core.agent.orchestrator import Orchestrator, active_confirm
from jarvis_core.agent.subagents import DEFAULT_SUBAGENTS, available_subagents
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMResponse, ToolCall
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult
from jarvis_core.tools.builtin.delegation import register_delegation_tool


class Herramienta(Tool):
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, nombre: str, sensible: bool = False) -> None:
        self.name = nombre
        self.description = nombre
        self.requires_confirmation = sensible
        self.ejecutada = False

    async def run(self, **kwargs) -> ToolResult:
        self.ejecutada = True
        return ToolResult(f"{self.name} hecho")


class ProveedorGuion:
    """Reproduce un guion de respuestas y anota qué herramientas vio cada vez."""

    def __init__(self, guion) -> None:
        self.guion = list(guion)
        self.herramientas_vistas: list[list[str]] = []
        self.prompts: list[str] = []

    async def complete(self, *, system, history, tools, on_text_delta=None, system_overlay=""):
        self.herramientas_vistas.append(sorted(t["name"] for t in tools))
        self.prompts.append(system)
        return self.guion.pop(0) if self.guion else LLMResponse(
            text="listo", stop_reason="end_turn", provider="fake"
        )


def _registro(*nombres, sensibles=()):
    registry = ToolRegistry()
    for nombre in nombres:
        registry.register(Herramienta(nombre, sensible=nombre in sensibles))
    return registry


CASA = ("query_connector_module", "run_connector_module_action", "list_connector_modules")
WEB = ("search_web", "fetch_web_page")


def test_solo_se_ofrecen_los_especialistas_que_pueden_hacer_algo():
    """Ofrecer el de casa sin conectores sería prometer una capacidad que no hay."""
    disponibles = available_subagents(_registro(*WEB))
    assert [s.name for s in disponibles] == ["investigacion"]


def test_un_especialista_solo_ve_sus_herramientas():
    registry = _registro(*CASA, *WEB, "read_file")
    specs = available_subagents(registry)
    register_delegation_tool(registry, ProveedorGuion([]), Settings(), specs)

    acotado = registry.subset(list(next(s for s in specs if s.name == "casa").tools))

    assert set(acotado.names()) == set(CASA)
    assert "search_web" not in acotado.names()
    assert "read_file" not in acotado.names()


def test_las_herramientas_del_subagente_son_los_mismos_objetos():
    """Copiarlas perdería el workspace confinado y la lista blanca del shell."""
    registry = _registro(*CASA)
    acotado = registry.subset(list(CASA))
    assert acotado.get(CASA[0]) is registry.get(CASA[0])


async def test_delegar_ejecuta_al_especialista_con_su_registro_acotado():
    registry = _registro(*CASA, *WEB)
    specs = available_subagents(registry)
    proveedor = ProveedorGuion(
        [
            # El subagente pide su herramienta y luego informa.
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="list_connector_modules", input={})],
                stop_reason="tool_use",
                provider="fake",
            ),
            LLMResponse(text="Todo apagado.", stop_reason="end_turn", provider="fake"),
        ]
    )
    register_delegation_tool(registry, proveedor, Settings(), specs)

    resultado = await registry.execute(
        "delegate_to_agent", {"agent": "casa", "task": "¿qué hay encendido?"}
    )

    assert "Todo apagado." in resultado.content
    assert "list_connector_modules" in resultado.content  # informa qué usó
    # Y solo vio las suyas: nada de web.
    assert proveedor.herramientas_vistas[0] == sorted(CASA)


async def test_un_subagente_no_puede_volver_a_delegar():
    """Sin esto, un despiste del modelo sería recursión infinita."""
    registry = _registro(*CASA)
    specs = available_subagents(registry)
    proveedor = ProveedorGuion([])
    register_delegation_tool(registry, proveedor, Settings(), specs)

    await registry.execute("delegate_to_agent", {"agent": "casa", "task": "x"})

    assert "delegate_to_agent" not in proveedor.herramientas_vistas[0]


async def test_una_accion_sensible_dentro_del_subagente_sigue_pidiendo_permiso():
    """Delegar no puede ser la puerta de atrás que se salta las aprobaciones."""
    registry = _registro(*CASA, sensibles=("run_connector_module_action",))
    specs = available_subagents(registry)
    proveedor = ProveedorGuion(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="run_connector_module_action", input={})],
                stop_reason="tool_use",
                provider="fake",
            ),
            LLMResponse(text="Hecho.", stop_reason="end_turn", provider="fake"),
        ]
    )
    register_delegation_tool(registry, proveedor, Settings(), specs)

    preguntado = []

    async def aprobar(nombre, argumentos):
        preguntado.append(nombre)
        return True

    token = active_confirm.set(aprobar)
    try:
        await registry.execute("delegate_to_agent", {"agent": "casa", "task": "enciende"})
    finally:
        active_confirm.reset(token)

    assert preguntado == ["run_connector_module_action"]
    assert registry.get("run_connector_module_action").ejecutada is True


async def test_sin_politica_de_confirmacion_la_accion_sensible_se_deniega():
    """La misma degradación segura que en el agente principal."""
    registry = _registro(*CASA, sensibles=("run_connector_module_action",))
    specs = available_subagents(registry)
    proveedor = ProveedorGuion(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="run_connector_module_action", input={})],
                stop_reason="tool_use",
                provider="fake",
            ),
            LLMResponse(text="No pude.", stop_reason="end_turn", provider="fake"),
        ]
    )
    register_delegation_tool(registry, proveedor, Settings(), specs)

    await registry.execute("delegate_to_agent", {"agent": "casa", "task": "enciende"})

    assert registry.get("run_connector_module_action").ejecutada is False


async def test_el_subagente_no_hereda_la_personalidad_del_principal():
    """Quien habla con el usuario es JARVIS; el subagente entrega un informe."""
    registry = _registro(*CASA)
    specs = available_subagents(registry)
    proveedor = ProveedorGuion([])
    register_delegation_tool(
        registry, proveedor, Settings(persona_name="JARVIS", language="es"), specs
    )

    await registry.execute("delegate_to_agent", {"agent": "casa", "task": "x"})

    prompt = proveedor.prompts[0]
    assert "especialista en la casa" in prompt
    assert "Trabajas para JARVIS" in prompt
    assert "set_emotion" not in prompt, "gastaría contexto sin aportar nada"


async def test_un_especialista_inexistente_se_reporta_sin_romper_el_turno():
    registry = _registro(*CASA)
    specs = available_subagents(registry)
    register_delegation_tool(registry, ProveedorGuion([]), Settings(), specs)

    resultado = await registry.execute(
        "delegate_to_agent", {"agent": "cocina", "task": "x"}
    )

    assert resultado.is_error
    assert "casa" in resultado.content  # dice cuáles hay


async def test_un_subagente_que_revienta_no_tumba_el_turno():
    class Explota:
        async def complete(self, **kwargs):
            raise RuntimeError("el proveedor se cayó")

    registry = _registro(*CASA)
    specs = available_subagents(registry)
    register_delegation_tool(registry, Explota(), Settings(), specs)

    resultado = await registry.execute("delegate_to_agent", {"agent": "casa", "task": "x"})

    assert resultado.is_error
    assert "falló" in resultado.content


def test_los_especialistas_tienen_menos_vueltas_que_el_principal():
    """Un encargo acotado no necesita 12 vueltas, y así un despiste no es una factura."""
    principal = Settings().max_tool_iterations
    assert all(s.max_iterations < principal for s in DEFAULT_SUBAGENTS)


def test_se_pueden_apagar():
    assert Settings(subagents_enabled=False).subagents_enabled is False

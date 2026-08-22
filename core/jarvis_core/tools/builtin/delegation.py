"""Delegar un encargo en un subagente especializado."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis_core.agent.subagents import SubagentSpec
from jarvis_core.config import Settings
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult

logger = logging.getLogger("jarvis.delegation")


class DelegateTool(Tool):
    """Lanza un subagente con su propio conjunto acotado de herramientas.

    El subagente hereda la política de confirmación del turno a través del
    contexto, así que una acción sensible dentro de él sigue pidiendo permiso
    igual que si la hiciera el agente principal. Y no puede volver a delegar:
    su registro no incluye esta herramienta, lo que corta de raíz la recursión.
    """

    name = "delegate_to_agent"

    def __init__(
        self,
        llm,
        settings: Settings,
        registry: ToolRegistry,
        specs: list[SubagentSpec],
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._registry = registry
        self._specs = {spec.name: spec for spec in specs}
        catalogo = "\n".join(f"- {s.name}: {s.purpose}" for s in specs)
        self.description = (
            "Delega un encargo acotado en un especialista y recibe su informe. "
            "Úsalo cuando la tarea caiga de lleno en el terreno de uno de ellos y "
            "requiera varios pasos; para una sola consulta directa, usa tú la "
            "herramienta correspondiente y ahorra la vuelta.\n\n"
            f"Especialistas disponibles:\n{catalogo}"
        )
        self.input_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": sorted(self._specs)},
                "task": {
                    "type": "string",
                    "description": (
                        "El encargo, completo y autónomo. El especialista no ve "
                        "vuestra conversación: incluye todo lo que necesite."
                    ),
                    "minLength": 3,
                },
            },
            "required": ["agent", "task"],
            "additionalProperties": False,
        }

    async def run(self, agent: str = "", task: str = "", **kwargs: Any) -> ToolResult:
        spec = self._specs.get(agent)
        if spec is None:
            return ToolResult(
                f"No existe el especialista '{agent}'. Hay: {', '.join(sorted(self._specs))}.",
                is_error=True,
            )
        if not task.strip():
            return ToolResult("El encargo no puede estar vacío.", is_error=True)

        # Importación local: el orquestador ya importa el registro, y hacerlo
        # arriba cerraría el círculo.
        from jarvis_core.agent.orchestrator import Orchestrator, active_confirm

        ajustes = replace_iterations(self._settings, spec.max_iterations)
        subagente = Orchestrator(
            self._llm,
            self._registry.subset(list(spec.tools)),
            ajustes,
            # La aprobación del turno en curso. Sin ella, una herramienta
            # sensible dentro del subagente se deniega sola: es la degradación
            # segura que ya aplica el agente principal.
            confirm=active_confirm.get(),
        )
        subagente._system = _subagent_prompt(self._settings, spec)

        logger.info("Delegando en el subagente %s", spec.name)
        try:
            respuesta = await subagente.send(task)
        except Exception as exc:
            logger.warning("El subagente %s falló", spec.name, exc_info=True)
            return ToolResult(f"El especialista {spec.name} falló: {exc}", is_error=True)

        herramientas = ", ".join(dict.fromkeys(respuesta.tools_used)) or "ninguna"
        return ToolResult(
            f"Informe de {spec.name} (herramientas: {herramientas}):\n{respuesta.text}"
        )


def replace_iterations(settings: Settings, iterations: int) -> Settings:
    """Copia de los ajustes con el tope de vueltas del subagente."""
    import dataclasses

    return dataclasses.replace(settings, max_tool_iterations=iterations)


def _subagent_prompt(settings: Settings, spec: SubagentSpec) -> str:
    """Prompt del especialista: corto y sin la personalidad del principal.

    Quien habla con el usuario es JARVIS. El subagente entrega un informe que su
    orquestador leerá, así que darle tono, emoción o reglas de conversación solo
    gastaría contexto y le invitaría a redactar de más.
    """
    idioma = {"es": "Responde en español.", "en": "Respond in English."}.get(
        settings.language, f"Responde en el idioma '{settings.language}'."
    )
    return (
        f"{spec.instructions}\n\n{idioma}\n\n"
        "Trabajas para JARVIS, no para el usuario final: entrega un informe "
        "breve y factual de lo que has hecho y averiguado, sin saludos ni "
        "ofrecimientos. Si no has podido completar el encargo, dilo claramente "
        "en vez de inventar un resultado. Usa solo las herramientas que tienes; "
        "si te falta alguna, indícalo y no lo simules."
    )


def register_delegation_tool(
    registry: ToolRegistry, llm, settings: Settings, specs: list[SubagentSpec]
) -> None:
    if not specs:
        return
    registry.register(DelegateTool(llm, settings, registry, specs))

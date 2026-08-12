"""Orquestador: el bucle de agente de JARVIS.

Implementa el bucle de uso de herramientas contra un `LLMProvider`:

    1. Llamar al modelo con el historial y las herramientas.
    2. Si el modelo pide herramientas, ejecutarlas y devolver los resultados.
    3. Repetir hasta que el modelo dé una respuesta final (o se alcance el límite).

Mantiene el historial de la conversación (memoria de corto plazo) en el propio objeto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMProvider
from jarvis_core.tools.base import ToolRegistry

logger = logging.getLogger("jarvis.orchestrator")

# Callback opcional para pedir confirmación antes de ejecutar herramientas sensibles.
# Recibe (nombre_herramienta, argumentos) y devuelve True para permitir.
ConfirmFn = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class AgentReply:
    """Respuesta final de un turno del agente."""

    text: str
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "end_turn"


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        settings: Settings,
        confirm: ConfirmFn | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._confirm = confirm
        self._system = settings.system_prompt()
        self._history: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Olvida la conversación actual (no la memoria a largo plazo)."""
        self._history = []

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._history

    async def send(self, user_message: str) -> AgentReply:
        """Procesa un mensaje del usuario y devuelve la respuesta final del agente."""
        self._history.append({"role": "user", "content": user_message})
        tools = self._registry.definitions()
        tools_used: list[str] = []

        for _ in range(self._settings.max_tool_iterations):
            response = await self._llm.complete(
                system=self._system,
                history=self._history,
                tools=tools,
            )
            # Añadir el turno del asistente al historial neutral. Se guarda el contenido
            # nativo (`raw`) para poder reutilizarlo si se sigue con el mismo proveedor.
            self._history.append(
                {
                    "role": "assistant",
                    "text": response.text,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "input": tc.input}
                        for tc in response.tool_calls
                    ],
                    "provider": response.provider,
                    "raw": response.assistant_content,
                }
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                return AgentReply(
                    text=response.text,
                    tools_used=tools_used,
                    stop_reason=response.stop_reason,
                )

            # Ejecutar todas las herramientas pedidas y devolver los resultados juntos.
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                tools_used.append(call.name)
                logger.info("Ejecutando herramienta %s con %s", call.name, call.input)

                allowed = await self._maybe_confirm(call.name, call.input)
                if not allowed:
                    results.append(
                        {
                            "id": call.id,
                            "name": call.name,
                            "content": "El usuario ha denegado la ejecución de esta herramienta.",
                            "is_error": True,
                        }
                    )
                    continue

                result = await self._registry.execute(call.name, call.input)
                results.append(
                    {
                        "id": call.id,
                        "name": call.name,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )

            self._history.append({"role": "tool", "results": results})

        # Si llegamos aquí, se agotó el número de iteraciones.
        return AgentReply(
            text="(He alcanzado el límite de pasos de herramientas sin terminar la tarea.)",
            tools_used=tools_used,
            stop_reason="max_iterations",
        )

    async def _maybe_confirm(self, name: str, arguments: dict[str, Any]) -> bool:
        tool = self._registry.get(name)
        if tool is None or not tool.requires_confirmation:
            return True
        if self._confirm is None:
            # Sin callback de confirmación, se permite (config del entorno decide qué
            # herramientas se registran). Cambia esto si quieres denegar por defecto.
            return True
        return await self._confirm(name, arguments)

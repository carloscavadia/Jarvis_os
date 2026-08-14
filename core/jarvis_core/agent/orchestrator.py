"""Orquestador: el bucle de agente de JARVIS.

Implementa el bucle de uso de herramientas contra un `LLMProvider`:

    1. Llamar al modelo con el historial y las herramientas.
    2. Si el modelo pide herramientas, ejecutarlas y devolver los resultados.
    3. Repetir hasta que el modelo dé una respuesta final (o se alcance el límite).

Mantiene el historial de la conversación (memoria de corto plazo) en el propio objeto.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMProvider, TextDeltaFn
from jarvis_core.tools.base import ToolRegistry, ToolResult

logger = logging.getLogger("jarvis.orchestrator")

# Callback opcional para pedir confirmación antes de ejecutar herramientas sensibles.
# Recibe (nombre_herramienta, argumentos) y devuelve True para permitir.
ConfirmFn = Callable[[str, dict[str, Any]], Awaitable[bool]]
ToolEventFn = Callable[[str, str, dict[str, Any], ToolResult | None], Awaitable[None]]


@dataclass
class AgentReply:
    """Respuesta final de un turno del agente."""

    text: str
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "end_turn"
    emotion: str = "neutral"


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        settings: Settings,
        confirm: ConfirmFn | None = None,
        emotion: EmotionState | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._confirm = confirm
        self._emotion = emotion
        self._system = settings.system_prompt()
        self._history: list[dict[str, Any]] = []
        self._send_lock = asyncio.Lock()

    def set_registry(self, registry: ToolRegistry) -> None:
        """Cambia las herramientas disponibles conservando la conversación.

        Se usa al registrar o quitar un conector: las herramientas que dependen
        de él aparecen o desaparecen sin obligar al usuario a empezar de cero.
        """
        self._registry = registry

    def reset(self) -> None:
        """Olvida la conversación actual (no la memoria a largo plazo)."""
        self._history = []

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._history

    async def send(
        self,
        user_message: str,
        on_text_delta: TextDeltaFn | None = None,
        confirm: ConfirmFn | None = None,
        on_tool_event: ToolEventFn | None = None,
    ) -> AgentReply:
        """Serializa los turnos de una sesión para no corromper su historial."""
        async with self._send_lock:
            return await self._send_locked(
                user_message, on_text_delta, confirm, on_tool_event
            )

    async def _send_locked(
        self,
        user_message: str,
        on_text_delta: TextDeltaFn | None = None,
        confirm: ConfirmFn | None = None,
        on_tool_event: ToolEventFn | None = None,
    ) -> AgentReply:
        """Procesa un mensaje del usuario y devuelve la respuesta final del agente."""
        self._history.append({"role": "user", "content": user_message})
        tools = self._registry.definitions()
        tools_used: list[str] = []

        for _ in range(self._settings.max_tool_iterations):
            response = await self._llm.complete(
                system=self._system,
                history=self._history,
                tools=tools,
                on_text_delta=on_text_delta,
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
                self._trim_history()
                return AgentReply(
                    text=response.text,
                    tools_used=tools_used,
                    stop_reason=response.stop_reason,
                    emotion=self._emotion.current if self._emotion else "neutral",
                )

            # Ejecutar todas las herramientas pedidas y devolver los resultados juntos.
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                tools_used.append(call.name)
                # Los argumentos pueden contener correos, mensajes o secretos operativos.
                logger.info("Ejecutando herramienta %s", call.name)
                if on_tool_event is not None:
                    await on_tool_event("proposed", call.name, call.input, None)

                allowed = await self._maybe_confirm(call.name, call.input, confirm)
                if not allowed:
                    if on_tool_event is not None:
                        await on_tool_event("denied", call.name, call.input, None)
                    results.append(
                        {
                            "id": call.id,
                            "name": call.name,
                            "content": "El usuario ha denegado la ejecución de esta herramienta.",
                            "is_error": True,
                        }
                    )
                    continue

                if on_tool_event is not None:
                    await on_tool_event("running", call.name, call.input, None)
                result = await self._registry.execute(call.name, call.input)
                if on_tool_event is not None:
                    await on_tool_event("completed", call.name, call.input, result)
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
        self._trim_history()
        return AgentReply(
            text="(He alcanzado el límite de pasos de herramientas sin terminar la tarea.)",
            tools_used=tools_used,
            stop_reason="max_iterations",
            emotion=self._emotion.current if self._emotion else "neutral",
        )

    def _trim_history(self) -> None:
        """Limita el contexto conservando turnos completos desde un mensaje de usuario."""
        limit = self._settings.max_history_items
        if len(self._history) <= limit:
            return
        trimmed = self._history[-limit:]
        while trimmed and trimmed[0].get("role") != "user":
            trimmed.pop(0)
        self._history = trimmed

    async def _maybe_confirm(
        self,
        name: str,
        arguments: dict[str, Any],
        confirm: ConfirmFn | None = None,
    ) -> bool:
        tool = self._registry.get(name)
        if tool is None or not tool.requires_confirmation:
            return True
        confirmation_policy = confirm or self._confirm
        if confirmation_policy is None:
            # Seguridad fail-closed: una herramienta sensible nunca se ejecuta si el
            # canal no proporcionó una política explícita de confirmación.
            logger.warning(
                "Herramienta sensible %s denegada: no hay política de confirmación",
                name,
            )
            return False
        return await confirmation_policy(name, arguments)

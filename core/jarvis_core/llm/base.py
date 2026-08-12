"""Interfaz genérica de proveedor de LLM.

El orquestador habla con esta interfaz, no con un SDK concreto. Así se puede cambiar el
'cerebro' (Claude, NVIDIA NIM, Ollama u otro compatible con OpenAI) sin tocar el bucle de
agente ni la memoria interna de JARVIS.

Historial NEUTRAL
-----------------
El orquestador mantiene el historial en un formato neutral (independiente del proveedor).
Cada elemento es un dict con una de estas formas:

    {"role": "user", "content": "<texto>"}

    {"role": "assistant",
     "text": "<texto visible>",
     "tool_calls": [{"id": ..., "name": ..., "input": {...}}, ...],
     "provider": "<id del proveedor que lo generó>",
     "raw": <contenido nativo, opcional>}

    {"role": "tool",
     "results": [{"id": ..., "name": ..., "content": "...", "is_error": bool}, ...]}

Cada proveedor traduce este historial a su formato nativo. Si un elemento de asistente trae
`raw` del mismo proveedor, el proveedor puede reutilizarlo (por ejemplo, para preservar los
bloques de razonamiento de Claude). Al cambiar de proveedor, se reconstruye desde `text` y
`tool_calls`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TextDeltaFn = Callable[[str], Awaitable[None]]
"""Callback asíncrono que recibe texto visible incremental del modelo."""


@dataclass
class ToolCall:
    """Una petición del modelo para ejecutar una herramienta."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Respuesta normalizada de un turno del modelo."""

    text: str
    """Texto visible concatenado."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    """Herramientas que el modelo quiere ejecutar en este turno."""

    stop_reason: str = "end_turn"
    """'end_turn', 'tool_use', 'max_tokens', 'refusal'..."""

    provider: str = ""
    """Id del proveedor que produjo la respuesta (p.ej. 'anthropic', 'openai')."""

    assistant_content: Any = None
    """Contenido nativo del turno, para reutilizarlo si se sigue con el mismo proveedor."""

    usage: dict[str, int] = field(default_factory=dict)
    """Contadores de tokens, si el proveedor los expone."""


class LLMProvider(Protocol):
    """Contrato que debe cumplir cualquier cerebro."""

    #: Identificador corto del proveedor.
    name: str

    async def complete(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaFn | None = None,
    ) -> LLMResponse:
        """Ejecuta un turno del modelo a partir del historial neutral.

        Args:
            system: prompt de sistema (personalidad + reglas).
            history: historial en formato neutral (ver arriba).
            tools: definiciones de herramientas (name, description, input_schema).
            on_text_delta: receptor opcional para mostrar la respuesta mientras se genera.
        """
        ...

"""Herramienta: JARVIS expresa su emoción (colorea el HUD).

Permite que el propio agente cambie su 'estado de ánimo', que la animación del HUD refleja
con el color del enjambre. Funciona con cualquier proveedor de IA (usa el bucle normal de
herramientas), sin parsear texto ni depender de salida estructurada.
"""

from __future__ import annotations

from typing import Any

from jarvis_core.agent.emotion import EmotionState, VALID_EMOTIONS
from jarvis_core.tools.base import Tool, ToolResult


class SetEmotionTool(Tool):
    name = "set_emotion"
    description = (
        "Fija tu emoción actual, que se refleja en tu animación (el color del enjambre). "
        "Llámala cuando tu estado de ánimo cambie, normalmente ANTES de responder: "
        "'focused' mientras razonas o trabajas duro; 'happy' al confirmar algo o dar buenas "
        "noticias; 'concern' ante un problema, error o advertencia; 'alert' cuando algo "
        "requiere atención del usuario; 'neutral' en reposo o conversación normal."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "emotion": {
                "type": "string",
                "enum": sorted(VALID_EMOTIONS),
                "description": "La emoción a mostrar.",
            }
        },
        "required": ["emotion"],
        "additionalProperties": False,
    }

    def __init__(self, state: EmotionState) -> None:
        self._state = state

    async def run(self, emotion: str = "", **kwargs: Any) -> ToolResult:
        if self._state.set(emotion):
            return ToolResult(content=f"Emoción fijada: {self._state.current}")
        return ToolResult(
            content=f"Emoción inválida. Usa una de: {', '.join(sorted(VALID_EMOTIONS))}.",
            is_error=True,
        )

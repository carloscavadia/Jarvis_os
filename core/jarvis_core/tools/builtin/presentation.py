"""Herramienta para presentar contenido estructurado en el HUD."""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult


class ShowInWorkspaceTool(Tool):
    name = "show_in_workspace"
    description = (
        "Abre el espacio de trabajo visual del HUD para mostrar texto, código, JSON, "
        "tablas o resultados extensos. Úsalo cuando el usuario pida ver algo allí o cuando "
        "una presentación separada sea claramente más útil que el chat."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Título breve de la ventana.",
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": 12000,
                "description": "Contenido exacto que se mostrará.",
            },
            "format": {
                "type": "string",
                "enum": ["text", "code", "json", "table", "markdown"],
            },
            "language": {
                "type": "string",
                "maxLength": 32,
                "description": "Lenguaje del código, si aplica.",
            },
            "keep_open": {
                "type": "boolean",
                "description": "Mantener visible hasta que el usuario cierre la ventana.",
            },
        },
        "required": ["title", "content"],
        "additionalProperties": False,
    }

    async def run(
        self,
        title: str = "",
        content: str = "",
        format: str = "text",
        language: str = "",
        keep_open: bool = True,
        **kwargs: Any,
    ) -> ToolResult:
        del language, keep_open
        title = title.strip()
        if not title or len(title) > 100:
            return ToolResult(content="Título de presentación inválido.", is_error=True)
        if not content or len(content) > 12000:
            return ToolResult(
                content="Contenido de presentación inválido.", is_error=True
            )
        if format not in {"text", "code", "json", "table", "markdown"}:
            return ToolResult(
                content="Formato de presentación inválido.", is_error=True
            )
        return ToolResult(content=f"Presentación mostrada en el HUD: {title}")

"""Herramienta para presentar contenido estructurado en el HUD."""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult


class ShowInWorkspaceTool(Tool):
    name = "show_in_workspace"
    description = (
        "Abre el pizarrón visual del HUD para mostrar código, JSON, tablas, listados o "
        "resultados extensos. Úsalo siempre que el contenido supere cuatro frases o sea "
        "estructurado. "
        "No lo uses para repetir lo que devolvió otra herramienta: eso ya se muestra "
        "solo en el pizarrón, y copiarlo cuesta miles de tokens sin añadir nada. "
        "Para una lista que compongas tú (dispositivos, tareas, canciones, archivos) usa "
        "format='json' con un array de objetos que compartan las mismas claves: el pizarrón "
        "deduce las columnas, agrupa por la categoría natural, cuenta cada grupo y ofrece un "
        "filtro. Un texto ya maquetado a mano pierde todo eso. "
        "El pizarrón y el chat se reparten el trabajo sin solaparse: aquí van los datos, "
        "y en el chat solo su lectura —cuántos hay, cómo se reparten, qué destaca—. No "
        "enumeres en el chat lo que ya está aquí, ni te limites a decir que está aquí."
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
                "description": (
                    "Contenido exacto que se mostrará. Con format='json', un array de "
                    "objetos con las mismas claves; incluye en cada uno el nombre legible "
                    "además del identificador técnico."
                ),
            },
            "format": {
                "type": "string",
                "enum": ["text", "code", "json", "table", "markdown"],
                "description": (
                    "'json' para listas de elementos: el pizarrón las agrupa y cuenta solo."
                ),
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

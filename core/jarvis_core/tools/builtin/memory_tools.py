"""Herramientas: guardar y recuperar memoria a largo plazo."""

from __future__ import annotations

from typing import Any

from jarvis_core.memory.store import MemoryStore
from jarvis_core.tools.base import Tool, ToolResult


class RememberTool(Tool):
    name = "remember"
    description = (
        "Guarda un hecho o preferencia del usuario en la memoria a largo plazo para "
        "recordarlo en futuras conversaciones. Usa una 'clave' corta y descriptiva "
        "(p.ej. 'cumpleaños_jefe') y un 'valor' con la información."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Identificador corto del hecho."},
            "value": {"type": "string", "description": "La información a recordar."},
            "tags": {"type": "string", "description": "Etiquetas opcionales, separadas por comas."},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    }

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    async def run(self, key: str = "", value: str = "", tags: str = "", **kwargs: Any) -> ToolResult:
        if not key or not value:
            return ToolResult(content="Faltan 'key' o 'value'.", is_error=True)
        self._memory.remember(key, value, tags)
        return ToolResult(content=f"Guardado: {key}")


class RecallTool(Tool):
    name = "recall"
    description = (
        "Recupera hechos guardados en la memoria a largo plazo. Con 'query' busca por "
        "texto; sin ella devuelve los más recientes."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Texto a buscar (opcional)."},
            "limit": {"type": "integer", "description": "Máximo de resultados (por defecto 10)."},
        },
        "additionalProperties": False,
    }

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    async def run(self, query: str = "", limit: int = 10, **kwargs: Any) -> ToolResult:
        results = self._memory.recall(query=query, limit=limit)
        if not results:
            return ToolResult(content="No hay nada en memoria para esa búsqueda.")
        lines = [f"- {m.key}: {m.value}" + (f"  [{m.tags}]" if m.tags else "") for m in results]
        return ToolResult(content="\n".join(lines))

"""Herramientas de música sobre un servidor Navidrome.

`play_music` no reproduce nada en el servidor: resuelve las canciones y devuelve
una **orden para el reproductor del HUD**, igual que `show_in_workspace` devuelve
una presentación para el pizarrón. El audio lo reproduce el dispositivo, que es
donde están los altavoces.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

from jarvis_core.music.navidrome import NavidromeClient, NavidromeError
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult

MAX_QUEUE = 50


class _MusicTool(Tool):
    def __init__(self, client: NavidromeClient) -> None:
        self.client = client

    async def _run_query(self, call, *args, **kwargs):
        """Ejecuta una consulta en un hilo y traduce el fallo a ToolResult."""
        try:
            return await asyncio.to_thread(call, *args, **kwargs), None
        except NavidromeError as exc:
            return None, ToolResult(str(exc), is_error=True)


class SearchMusicTool(_MusicTool):
    name = "search_music"
    description = (
        "Busca canciones en la biblioteca de música por título, artista o álbum. "
        "Úsala para saber qué hay disponible antes de reproducir, o cuando el usuario "
        "pregunte por su música. No reproduce nada."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def run(self, query: str = "", limit: int = 20, **kwargs: Any) -> ToolResult:
        del kwargs
        limit = max(1, min(int(limit or 20), 50))
        songs, error = await self._run_query(self.client.search, query, limit)
        if error is not None:
            return error
        if not songs:
            return ToolResult(f"No encontré nada para «{query}» en tu biblioteca.")
        return ToolResult(
            json.dumps({"count": len(songs), "songs": songs}, ensure_ascii=False)
        )


class PlayMusicTool(_MusicTool):
    name = "play_music"
    description = (
        "Reproduce música en el reproductor del HUD. Con `query` busca y reproduce lo "
        "que mejor encaje; sin `query` pone una selección aleatoria de la biblioteca. "
        "Úsala cuando el usuario pida escuchar algo. Después responde en una frase "
        "corta diciendo qué suena; no enumeres la lista."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "Canción, artista o álbum. Vacío = aleatorio.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_QUEUE,
                "description": "Cuántas canciones encolar (por defecto 20).",
            },
        },
        "additionalProperties": False,
    }

    async def run(self, query: str = "", limit: int = 20, **kwargs: Any) -> ToolResult:
        del kwargs
        query = (query or "").strip()
        limit = max(1, min(int(limit or 20), MAX_QUEUE))
        if query:
            songs, error = await self._run_query(self.client.search, query, limit)
        else:
            songs, error = await self._run_query(self.client.random, limit)
        if error is not None:
            return error
        # Sin id no se puede pedir el audio: encolarlas rompería el reproductor.
        songs = [song for song in songs if song.get("id")]
        if not songs:
            return ToolResult(
                f"No encontré «{query}» en tu biblioteca."
                if query
                else "Tu biblioteca de música está vacía."
            )
        return ToolResult(
            json.dumps(
                {
                    "queue": songs[:limit],
                    "source": query or "aleatorio",
                    "now_playing": songs[0],
                },
                ensure_ascii=False,
            )
        )


class MusicPlaybackTool(Tool):
    """Controla el reproductor ya abierto: pausa, siguiente, parar…"""

    name = "control_music"
    description = (
        "Controla el reproductor del HUD que ya está sonando: pausar, reanudar, "
        "siguiente, anterior o parar. No sirve para empezar a reproducir; para eso "
        "usa play_music."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["pause", "resume", "next", "previous", "stop"],
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def run(self, command: str = "", **kwargs: Any) -> ToolResult:
        del kwargs
        if command not in {"pause", "resume", "next", "previous", "stop"}:
            return ToolResult("Comando de reproducción no válido.", is_error=True)
        return ToolResult(json.dumps({"command": command}, ensure_ascii=False))


def register_music_tools(
    registry: ToolRegistry, client: NavidromeClient | None
) -> None:
    """Registra las herramientas solo si hay servidor de música configurado.

    Sin él no se registran: ofrecerle a JARVIS herramientas que siempre fallan
    solo consigue que las intente y se disculpe.
    """
    if client is None:
        return
    registry.register(SearchMusicTool(client))
    registry.register(PlayMusicTool(client))
    registry.register(MusicPlaybackTool())

"""Herramientas de música sobre un servidor Navidrome (protocolo Subsonic).

`play_music` no reproduce nada en el servidor: resuelve las canciones y devuelve
una **orden para el reproductor del HUD**, igual que `show_in_workspace` devuelve
una presentación para el pizarrón. El audio lo reproduce el dispositivo, que es
donde están los altavoces.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from jarvis_core.connectors.runtime import ConnectorRuntime
    from jarvis_core.connectors.store import ConnectorStore

MAX_QUEUE = 50


class _MusicTool(Tool):
    def __init__(self, runtime: ConnectorRuntime, connector: str) -> None:
        self.runtime = runtime
        self.connector = connector

    async def _songs(self, action: str, payload: dict[str, Any]) -> tuple[
        list[dict[str, Any]], ToolResult | None
    ]:
        result = await self.runtime.invoke(
            self.connector, action, payload, write=False
        )
        if result.is_error:
            return [], result
        try:
            songs = json.loads(result.content).get("songs", [])
        except (json.JSONDecodeError, AttributeError):
            return [], ToolResult(
                "El servidor de música devolvió una respuesta ilegible.", is_error=True
            )
        return [song for song in songs if song.get("id")], None


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
        songs, error = await self._songs(
            "music.search", {"query": query, "limit": limit}
        )
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
            songs, error = await self._songs(
                "music.search", {"query": query, "limit": limit}
            )
        else:
            songs, error = await self._songs("music.random", {"limit": limit})
        if error is not None:
            return error
        if not songs:
            return ToolResult(
                f"No encontré «{query}» en tu biblioteca." if query
                else "Tu biblioteca de música está vacía.",
            )
        # `queue` es la orden que el gateway reenvía al reproductor del HUD.
        return ToolResult(
            json.dumps(
                {
                    "queue": songs[:limit],
                    "connector": self.connector,
                    "source": query or "aleatorio",
                    "now_playing": songs[0],
                },
                ensure_ascii=False,
            )
        )


class MusicPlaybackTool(Tool):
    """Controla el reproductor ya abierto: pausa, siguiente, volumen…"""

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


def find_music_connector(store: ConnectorStore) -> str | None:
    """Nombre del primer módulo de música activo, si hay alguno."""
    for module in store.list_public():
        if module["type"] == "navidrome" and module["enabled"]:
            return str(module["name"])
    return None


def register_music_tools(
    registry: ToolRegistry, store: ConnectorStore, runtime: ConnectorRuntime
) -> None:
    """Registra las herramientas solo si hay un servidor de música configurado.

    Sin módulo no se registran: ofrecerle a JARVIS herramientas que siempre
    fallan solo consigue que las intente y se disculpe.
    """
    connector = find_music_connector(store)
    if connector is None:
        return
    registry.register(SearchMusicTool(runtime, connector))
    registry.register(PlayMusicTool(runtime, connector))
    registry.register(MusicPlaybackTool())

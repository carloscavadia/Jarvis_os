"""Tests de las herramientas de música.

`play_music` no reproduce nada en el servidor: devuelve una orden para el
reproductor del HUD. Estos tests fijan ese contrato.
"""

import json

from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.tools.base import ToolRegistry, ToolResult
from jarvis_core.tools.builtin.music import (
    PlayMusicTool,
    SearchMusicTool,
    find_music_connector,
    register_music_tools,
)

SONGS = [
    {"id": "1", "title": "Uno", "artist": "A"},
    {"id": "2", "title": "Dos", "artist": "B"},
]


class FakeRuntime:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"songs": SONGS}
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    async def invoke(self, connector, action, payload, *, write):
        self.calls.append((connector, action, payload))
        assert write is False, "la música es solo de lectura"
        if self.error is not None:
            return ToolResult(self.error, is_error=True)
        return ToolResult(json.dumps(self.payload))


# ── search_music ─────────────────────────────────────────────────────────────


async def test_search_returns_the_songs_found():
    runtime = FakeRuntime()
    result = await SearchMusicTool(runtime, "musica").run(query="uno")
    assert not result.is_error
    assert json.loads(result.content)["count"] == 2
    assert runtime.calls[0][1] == "music.search"


async def test_search_says_so_when_there_is_nothing():
    result = await SearchMusicTool(FakeRuntime({"songs": []}), "musica").run(query="zzz")
    assert not result.is_error
    assert "No encontré" in result.content


# ── play_music ───────────────────────────────────────────────────────────────


async def test_play_with_query_searches_and_returns_a_queue():
    runtime = FakeRuntime()
    result = await PlayMusicTool(runtime, "musica").run(query="queen")
    payload = json.loads(result.content)
    assert runtime.calls[0][1] == "music.search"
    assert payload["queue"] == SONGS
    assert payload["now_playing"] == SONGS[0]
    assert payload["connector"] == "musica"


async def test_play_without_query_uses_random():
    runtime = FakeRuntime()
    await PlayMusicTool(runtime, "musica").run()
    assert runtime.calls[0][1] == "music.random"


async def test_play_drops_songs_without_id():
    # Sin id no se puede pedir el audio al servidor; encolarlas rompería el HUD.
    runtime = FakeRuntime({"songs": [{"title": "rota"}, SONGS[0]]})
    result = await PlayMusicTool(runtime, "musica").run(query="x")
    assert json.loads(result.content)["queue"] == [SONGS[0]]


async def test_play_respects_the_limit():
    runtime = FakeRuntime({"songs": [{"id": str(i), "title": str(i)} for i in range(40)]})
    result = await PlayMusicTool(runtime, "musica").run(query="x", limit=3)
    assert len(json.loads(result.content)["queue"]) == 3


async def test_play_reports_a_connector_error_instead_of_an_empty_queue():
    result = await PlayMusicTool(FakeRuntime(error="servidor caído"), "musica").run()
    assert result.is_error


async def test_play_handles_an_unreadable_response():
    class Broken(FakeRuntime):
        async def invoke(self, *args, **kwargs):
            return ToolResult("no-es-json")

    result = await PlayMusicTool(Broken(), "musica").run(query="x")
    assert result.is_error


# ── Registro ─────────────────────────────────────────────────────────────────


def test_tools_are_not_registered_without_a_music_module(tmp_path):
    """Ofrecer herramientas que siempre fallan solo hace que las intente."""
    store = ConnectorStore(str(tmp_path / "c.db"), "m" * 32)
    store.upsert("casa", "home_assistant", {"url": "http://ha:8123"}, {"token": "t"})
    registry = ToolRegistry()
    register_music_tools(registry, store, FakeRuntime())
    assert registry.names() == []
    assert find_music_connector(store) is None


def test_tools_are_registered_when_a_module_exists(tmp_path):
    store = ConnectorStore(str(tmp_path / "c.db"), "m" * 32)
    store.upsert(
        "musica", "navidrome", {"url": "http://nav:4533", "username": "c"}, {"password": "p"}
    )
    registry = ToolRegistry()
    register_music_tools(registry, store, FakeRuntime())
    assert set(registry.names()) == {"search_music", "play_music", "control_music"}
    assert find_music_connector(store) == "musica"


def test_a_disabled_module_does_not_register_tools(tmp_path):
    store = ConnectorStore(str(tmp_path / "c.db"), "m" * 32)
    store.upsert(
        "musica",
        "navidrome",
        {"url": "http://nav:4533", "username": "c"},
        {"password": "p"},
        enabled=False,
    )
    assert find_music_connector(store) is None

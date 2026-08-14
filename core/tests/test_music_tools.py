"""Tests del cliente de Navidrome y de las herramientas de música.

`play_music` no reproduce nada en el servidor: devuelve una orden para el
reproductor del HUD. Estos tests fijan ese contrato y el del protocolo Subsonic.
"""

import hashlib
import io
import json
from email.message import Message

import pytest
from jarvis_core.config import Settings
from jarvis_core.music.navidrome import (
    NavidromeClient,
    NavidromeError,
    build_navidrome_client,
    subsonic_auth_params,
)
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin.music import (
    MusicPlaybackTool,
    PlayMusicTool,
    SearchMusicTool,
    register_music_tools,
)

SONG = {
    "id": "42",
    "title": "Bohemian Rhapsody",
    "artist": "Queen",
    "album": "A Night at the Opera",
    "duration": 355,
    "coverArt": "al-1",
}


# ── Protocolo Subsonic ───────────────────────────────────────────────────────


def test_token_follows_the_protocol():
    """token = md5(contraseña + sal), en hexadecimal minúsculas."""
    params = subsonic_auth_params("carlos", "sesame")
    assert params["t"] == hashlib.md5(f"sesame{params['s']}".encode()).hexdigest()
    assert params["u"] == "carlos"
    assert params["f"] == "json"
    assert "p" not in params  # la contraseña nunca viaja


def test_salt_changes_every_call():
    assert len({subsonic_auth_params("u", "p")["s"] for _ in range(20)}) == 20


def test_client_requires_full_credentials():
    with pytest.raises(NavidromeError):
        NavidromeClient("", "u", "p")
    with pytest.raises(NavidromeError):
        NavidromeClient("http://nav:4533", "", "p")
    with pytest.raises(NavidromeError):
        NavidromeClient("http://nav:4533", "u", "")


def test_client_rejects_a_url_with_credentials_or_query():
    with pytest.raises(NavidromeError):
        NavidromeClient("http://user:pass@nav:4533", "u", "p")
    with pytest.raises(NavidromeError):
        NavidromeClient("http://nav:4533/?x=1", "u", "p")
    with pytest.raises(NavidromeError):
        NavidromeClient("ftp://nav:4533", "u", "p")


def test_media_url_is_authenticated_and_hides_the_password():
    client = NavidromeClient("http://nav:4533", "carlos", "sesame")
    url = client.endpoint_url("stream", {"id": "42"})
    assert url.startswith("http://nav:4533/rest/stream?")
    assert "id=42" in url and "u=carlos" in url and "t=" in url
    assert "sesame" not in url


# ── Consultas ────────────────────────────────────────────────────────────────


class _Opener:
    def __init__(self, body):
        self.body = json.dumps(body).encode()
        self.request = None

    def open(self, request, timeout):
        del timeout
        self.request = request

        class FakeResponse(io.BytesIO):
            headers = Message()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        return FakeResponse(self.body)


def client_with(body):
    client = NavidromeClient("http://nav:4533", "carlos", "sesame")
    client._opener = _Opener(body)
    return client


def test_search_compacts_songs_and_tolerates_missing_fields():
    client = client_with(
        {
            "subsonic-response": {
                "status": "ok",
                # La segunda pista está incompleta: no debe tumbar la búsqueda.
                "searchResult3": {"song": [SONG, {"id": "43", "title": "Suelta"}]},
            }
        }
    )
    songs = client.search("queen")
    assert songs[0] == {
        "id": "42",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "duration": 355,
        "cover_art": "al-1",
    }
    assert songs[1]["artist"] == ""
    assert "search3" in client._opener.request.full_url


def test_failure_reports_the_real_reason():
    """Subsonic devuelve 200 con el error dentro; sin leerlo parecería un éxito."""
    client = client_with(
        {
            "subsonic-response": {
                "status": "failed",
                "error": {"code": 40, "message": "Wrong username or password"},
            }
        }
    )
    with pytest.raises(NavidromeError, match="Wrong username or password"):
        client.search("queen")


def test_ping_returns_the_protocol_version():
    client = client_with({"subsonic-response": {"status": "ok", "version": "1.16.1"}})
    assert client.ping() == "1.16.1"


def test_random_and_playlists_read_their_own_envelopes():
    client = client_with(
        {"subsonic-response": {"status": "ok", "randomSongs": {"song": [SONG]}}}
    )
    assert client.random(5)[0]["id"] == "42"

    client = client_with(
        {
            "subsonic-response": {
                "status": "ok",
                "playlists": {"playlist": [{"id": "9", "name": "Coche", "songCount": 12}]},
            }
        }
    )
    assert client.playlists() == [{"id": "9", "name": "Coche", "songs": 12}]


# ── Construcción desde configuración ─────────────────────────────────────────


def test_no_client_without_configuration():
    assert build_navidrome_client(Settings()) is None


def test_no_client_when_credentials_are_incomplete():
    # Una URL sin usuario es un error de configuración, no un motivo de caída.
    assert build_navidrome_client(Settings(navidrome_url="http://nav:4533")) is None


def test_client_is_built_from_settings():
    client = build_navidrome_client(
        Settings(
            navidrome_url="http://nav:4533",
            navidrome_username="carlos",
            navidrome_password="sesame",
        )
    )
    assert isinstance(client, NavidromeClient)


# ── Herramientas ─────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, songs=None, error=None):
        self.songs = songs if songs is not None else [{"id": "1", "title": "Uno"}]
        self.error = error
        self.calls: list[str] = []

    def _maybe_fail(self):
        if self.error:
            raise NavidromeError(self.error)

    def search(self, query, limit=20):
        self.calls.append("search")
        self._maybe_fail()
        return self.songs[:limit]

    def random(self, limit=20, genre=""):
        self.calls.append("random")
        self._maybe_fail()
        return self.songs[:limit]


async def test_search_returns_what_it_finds():
    tool = SearchMusicTool(FakeClient())
    result = await tool.run(query="uno")
    assert not result.is_error
    assert json.loads(result.content)["count"] == 1


async def test_search_says_so_when_there_is_nothing():
    result = await SearchMusicTool(FakeClient(songs=[])).run(query="zzz")
    assert not result.is_error
    assert "No encontré" in result.content


async def test_play_with_query_searches():
    client = FakeClient()
    result = await PlayMusicTool(client).run(query="queen")
    assert client.calls == ["search"]
    assert json.loads(result.content)["queue"] == client.songs


async def test_play_without_query_uses_random():
    client = FakeClient()
    await PlayMusicTool(client).run()
    assert client.calls == ["random"]


async def test_play_drops_songs_without_id():
    # Sin id no se puede pedir el audio; encolarlas rompería el reproductor.
    client = FakeClient(songs=[{"title": "rota"}, {"id": "5", "title": "buena"}])
    result = await PlayMusicTool(client).run(query="x")
    assert json.loads(result.content)["queue"] == [{"id": "5", "title": "buena"}]


async def test_play_reports_the_server_error():
    result = await PlayMusicTool(FakeClient(error="servidor caído")).run()
    assert result.is_error
    assert "servidor caído" in result.content


async def test_control_only_accepts_known_commands():
    assert not (await MusicPlaybackTool().run(command="pause")).is_error
    assert (await MusicPlaybackTool().run(command="formatear")).is_error


# ── Registro ─────────────────────────────────────────────────────────────────


def test_tools_are_not_registered_without_a_server():
    """Ofrecer herramientas que siempre fallan solo hace que las intente."""
    registry = ToolRegistry()
    register_music_tools(registry, None)
    assert registry.names() == []


def test_tools_are_registered_with_a_server():
    registry = ToolRegistry()
    register_music_tools(registry, FakeClient())
    assert set(registry.names()) == {"search_music", "play_music", "control_music"}

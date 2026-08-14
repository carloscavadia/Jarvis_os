"""Cliente de Navidrome (protocolo Subsonic).

Se configura por `.env`, no por el panel de conectores: es un servidor propio y
fijo, y pedirle al usuario que lo registre a mano cada vez que reconstruye el
contenedor no aporta nada.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import certifi

#: Versión del protocolo Subsonic que declara el cliente.
SUBSONIC_VERSION = "1.16.1"
SUBSONIC_CLIENT = "jarvis-os"


class NavidromeError(RuntimeError):
    """Fallo de configuración o de comunicación con el servidor de música."""


def validate_music_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if len(raw_url) > 2000 or any(ord(char) < 32 for char in raw_url):
        raise NavidromeError("La URL del servidor de música no es válida.")
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise NavidromeError("El servidor de música requiere una URL HTTP o HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NavidromeError("La URL no admite credenciales, query ni fragmento.")
    return urlunsplit(parsed)


def subsonic_auth_params(username: str, password: str) -> dict[str, str]:
    """Parámetros de autenticación de Subsonic.

    El protocolo usa `t = md5(contraseña + sal)` con una sal aleatoria por
    petición, de forma que la contraseña nunca viaja. MD5 aquí no es una
    decisión nuestra: lo fija el protocolo.
    """
    salt = secrets.token_hex(8)
    token = hashlib.md5(f"{password}{salt}".encode()).hexdigest()
    return {
        "u": username,
        "t": token,
        "s": salt,
        "v": SUBSONIC_VERSION,
        "c": SUBSONIC_CLIENT,
        "f": "json",
    }


def _song(item: dict[str, Any]) -> dict[str, Any]:
    """Compacta una canción a lo que el agente y el reproductor necesitan.

    Los campos se leen con `get` porque un catálogo real tiene pistas sin álbum,
    sin año o sin carátula, y una sola no debe tumbar toda la búsqueda.
    """
    song = {
        "id": str(item.get("id", "")),
        "title": str(item.get("title") or "(sin título)"),
        "artist": str(item.get("artist") or ""),
        "album": str(item.get("album") or ""),
    }
    duration = item.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        song["duration"] = int(duration)
    if item.get("coverArt"):
        song["cover_art"] = str(item["coverArt"])
    if item.get("year"):
        song["year"] = str(item["year"])
    return song


class NavidromeClient:
    """Consultas de solo lectura contra la biblioteca de música."""

    MAX_RESPONSE_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        timeout: float = 20.0,
    ) -> None:
        if not url or not username or not password:
            raise NavidromeError(
                "Faltan JARVIS_NAVIDROME_URL, _USERNAME o _PASSWORD."
            )
        self.url = validate_music_url(url)
        self.username = username
        self.password = password
        self.timeout = timeout
        tls = ssl.create_default_context(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=tls)
        )

    def endpoint_url(self, endpoint: str, params: dict[str, str]) -> str:
        """URL autenticada de un endpoint Subsonic."""
        base = self.url.rstrip("/") + "/"
        auth = subsonic_auth_params(self.username, self.password)
        return urljoin(base, f"rest/{endpoint}") + "?" + urlencode(auth | params)

    def _call(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint_url(endpoint, params),
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "JARVIS-OS/0.3 navidrome",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            raise NavidromeError(
                f"El servidor de música respondió HTTP {exc.code}."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NavidromeError(
                f"No pude contactar el servidor de música: {type(exc).__name__}."
            ) from exc
        try:
            body = json.loads(raw).get("subsonic-response", {})
        except (json.JSONDecodeError, AttributeError) as exc:
            raise NavidromeError(
                "El servidor de música devolvió una respuesta ilegible."
            ) from exc
        if body.get("status") != "ok":
            # Subsonic informa el motivo real aquí; sin leerlo solo se vería un 200.
            message = body.get("error", {}).get("message", "motivo desconocido")
            raise NavidromeError(f"El servidor de música rechazó la consulta: {message}")
        return body

    # ── Consultas ────────────────────────────────────────────────────────────

    def ping(self) -> str:
        return str(self._call("ping", {}).get("version", "?"))

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        body = self._call(
            "search3",
            {
                "query": query[:200],
                "songCount": str(limit),
                "albumCount": "0",
                "artistCount": "0",
            },
        )
        songs = body.get("searchResult3", {}).get("song", []) or []
        return [_song(item) for item in songs if isinstance(item, dict)]

    def random(self, limit: int = 20, genre: str = "") -> list[dict[str, Any]]:
        params = {"size": str(limit)}
        if genre.strip():
            params["genre"] = genre.strip()[:100]
        body = self._call("getRandomSongs", params)
        songs = body.get("randomSongs", {}).get("song", []) or []
        return [_song(item) for item in songs if isinstance(item, dict)]

    def playlists(self) -> list[dict[str, Any]]:
        body = self._call("getPlaylists", {})
        items = body.get("playlists", {}).get("playlist", []) or []
        return [
            {
                "id": str(item.get("id", "")),
                "name": str(item.get("name") or "(sin nombre)"),
                "songs": int(item.get("songCount") or 0),
            }
            for item in items
            if isinstance(item, dict)
        ]

    def playlist(self, playlist_id: str, limit: int = 50) -> dict[str, Any]:
        body = self._call("getPlaylist", {"id": playlist_id})
        playlist = body.get("playlist", {})
        entries = playlist.get("entry", []) or []
        songs = [_song(item) for item in entries if isinstance(item, dict)][:limit]
        return {"name": str(playlist.get("name") or ""), "songs": songs}


def build_navidrome_client(settings: Any) -> NavidromeClient | None:
    """Cliente a partir de la configuración, o None si no está configurado."""
    if not settings.navidrome_url:
        return None
    try:
        return NavidromeClient(
            settings.navidrome_url,
            settings.navidrome_username,
            settings.navidrome_password,
            timeout=settings.navidrome_timeout_seconds,
        )
    except NavidromeError:
        return None

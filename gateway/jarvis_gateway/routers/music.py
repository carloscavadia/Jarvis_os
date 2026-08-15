"""Endpoints de música: proxy de Navidrome y consultas a la biblioteca.

El audio pasa por el gateway y no va directo del navegador a Navidrome por dos
razones: `<audio src>` no admite cabeceras, así que la alternativa sería
entregarle la contraseña del servidor de música; y una canción no cabe —ni debe
caber— en los límites de respuesta pensados para JSON.

Los servicios se leen del módulo `runtime` y no se importan por nombre: un
`from ... import settings` congela la referencia en el import y sustituirla
después, como hacen los tests, no tendría efecto aquí.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from jarvis_core.music.navidrome import build_navidrome_client

from jarvis_gateway import runtime

logger = logging.getLogger("jarvis.gateway.music")
router = APIRouter(prefix="/music", tags=["music"])


_MEDIA_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


async def _proxy_media(
    endpoint: str, params: dict[str, str], range_header: str | None = None
):
    """Sirve audio o carátula desde Navidrome sin exponer sus credenciales.

    El navegador no puede poner cabeceras en `<audio src>`, así que la
    alternativa sería mandarle la contraseña del servidor de música. Con el proxy
    solo circula la clave del gateway, que es la que el HUD ya tiene.

    La petición de rango se reenvía tal cual y su respuesta se devuelve íntegra:
    sin eso `<audio>` no conoce la duración —la barra de progreso se queda en
    cero— ni puede avanzar dentro de una canción, porque saltar a un minuto
    concreto es exactamente pedir un rango de bytes.
    """
    client = build_navidrome_client(runtime.settings)
    if client is None:
        raise HTTPException(
            status_code=404, detail="No hay servidor de música configurado."
        )
    url = client.endpoint_url(endpoint, params)

    media_headers = {"User-Agent": "JARVIS-OS/0.3 navidrome"}
    if range_header:
        media_headers["Range"] = range_header

    def fetch():
        request = urllib.request.Request(url, method="GET", headers=media_headers)
        return urllib.request.urlopen(
            request, timeout=runtime.settings.navidrome_timeout_seconds
        )

    try:
        upstream = await asyncio.to_thread(fetch)
    except urllib.error.HTTPError as exc:
        # Un rango imposible es una respuesta legítima del protocolo, no un
        # fallo: traducirlo a 502 acusaría a Navidrome de estar caído.
        exc.close()
        if exc.code == 416:
            raise HTTPException(
                status_code=416, detail="Rango solicitado no disponible."
            ) from exc
        logger.warning(
            "El servidor de música respondió HTTP %s a %s", exc.code, endpoint
        )
        raise HTTPException(
            status_code=502, detail="El servidor de música no respondió."
        ) from exc
    except Exception as exc:
        logger.warning("No pude obtener %s del servidor de música: %s", endpoint, exc)
        raise HTTPException(
            status_code=502, detail="El servidor de música no respondió."
        ) from exc

    media_type = upstream.headers.get_content_type() or "application/octet-stream"
    media_response_headers = {"Cache-Control": "private, max-age=300"}
    # `Accept-Ranges` dice que se puede saltar; `Content-Length` y
    # `Content-Range`, cuánto dura y qué trozo llega.
    for header in ("Accept-Ranges", "Content-Length", "Content-Range"):
        value = upstream.headers.get(header)
        if value:
            media_response_headers[header] = value

    def chunks():
        # Se transmite por partes: una canción no cabe —ni debe caber— en los
        # límites de respuesta pensados para JSON.
        try:
            while True:
                block = upstream.read(64 * 1024)
                if not block:
                    return
                yield block
        finally:
            upstream.close()

    return StreamingResponse(
        chunks(),
        status_code=getattr(upstream, "status", 200) or 200,
        media_type=media_type,
        headers=media_response_headers,
    )


@router.get("/stream/{song_id}")
async def music_stream(
    song_id: str,
    token: str = "",
    range_header: str | None = Header(default=None, alias="Range"),
):
    # El `<audio>` del navegador no admite cabeceras: la clave viaja por query,
    # igual que ya hace el WebSocket del HUD.
    if not runtime.valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    if not _MEDIA_ID_RE.fullmatch(song_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    return await _proxy_media("stream", {"id": song_id}, range_header)


@router.get("/cover/{cover_id}")
async def music_cover(cover_id: str, token: str = "", size: int = 256):
    if not runtime.valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    if not _MEDIA_ID_RE.fullmatch(cover_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    return await _proxy_media(
        "getCoverArt", {"id": cover_id, "size": str(max(32, min(size, 1024)))}
    )


# ── Endpoints adicionales de Música (Navidrome) ──

@router.get("/status", dependencies=[Depends(runtime.require_api_key)])
async def music_status():
    client = build_navidrome_client(runtime.settings)
    if client is None:
        return {"enabled": False, "version": None}
    try:
        ver = await asyncio.to_thread(client.ping)
        return {"enabled": True, "version": ver}
    except Exception as exc:
        return {"enabled": False, "error": str(exc)}


@router.get("/playlists", dependencies=[Depends(runtime.require_api_key)])
async def music_playlists():
    client = build_navidrome_client(runtime.settings)
    if client is None:
        return {"playlists": []}
    try:
        items = await asyncio.to_thread(client.playlists)
        return {"playlists": items}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/playlist/{playlist_id}", dependencies=[Depends(runtime.require_api_key)])
async def music_playlist(playlist_id: str):
    client = build_navidrome_client(runtime.settings)
    if client is None:
        raise HTTPException(status_code=404, detail="Música no configurada.")
    try:
        data = await asyncio.to_thread(client.playlist, playlist_id)
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/search", dependencies=[Depends(runtime.require_api_key)])
async def music_search(query: str = "", limit: int = 20):
    client = build_navidrome_client(runtime.settings)
    if client is None:
        return {"songs": []}
    try:
        songs = await asyncio.to_thread(client.search, query, limit)
        return {"songs": songs}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/random", dependencies=[Depends(runtime.require_api_key)])
async def music_random(limit: int = 20):
    client = build_navidrome_client(runtime.settings)
    if client is None:
        return {"songs": []}
    try:
        songs = await asyncio.to_thread(client.random, limit)
        return {"songs": songs}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Endpoints de Home Assistant Táctil ──

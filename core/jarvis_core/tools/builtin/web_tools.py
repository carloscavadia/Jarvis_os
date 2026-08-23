"""Acceso web público de solo lectura con protección contra SSRF."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, ClassVar

import certifi

from jarvis_core.tools.base import Tool, ToolResult

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan", ".home", ".arpa")
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
)


def validate_public_https_url(raw_url: str) -> str:
    """Acepta únicamente HTTPS público y rechaza cualquier resolución no global."""
    raw_url = raw_url.strip()
    if len(raw_url) > 2000 or any(ord(char) < 32 for char in raw_url):
        raise ValueError("La URL no es válida.")
    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Solo se permiten URLs HTTPS públicas.")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("La URL contiene credenciales o un puerto no permitido.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError("El destino local o interno está bloqueado.")
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el dominio público.") from exc
    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global for address in addresses
    ):
        raise ValueError("El destino resuelve a una red privada o reservada.")
    return urllib.parse.urlunsplit(parsed)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._parts: list[str] = []
        self._ignored = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        if self._in_title:
            self.title += data
        else:
            self._parts.append(data)

    def text(self) -> str:
        lines = (re.sub(r"\s+", " ", part).strip() for part in self._parts)
        return "\n".join(line for line in lines if line)


class WebClient:
    def __init__(self, timeout: float, max_bytes: int) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        tls_context = ssl.create_default_context(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=tls_context),
        )

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        redirects: int = 3,
    ) -> tuple[str, str, bytes, bool]:
        """Descarga una página pública. Devuelve además si hubo que recortarla.

        Antes, pasarse del tope era un error: «La página supera el límite de
        descarga», y no se leía nada. Eso no protegía de nada que recortar no
        proteja igual —el tope existe para acotar la memoria, y en los dos casos
        se leen los mismos bytes y se para ahí—, pero convertía cualquier página
        grande en una herramienta que no funciona. Una portada de YouTube pasa de
        un mega de sobra, y con ella se caía la vista previa entera.

        Ahora se corta y se dice. El principio de una página es justo donde está
        el título y el texto de cabecera, que es lo que se quería leer.
        """
        current = validate_public_https_url(url)
        request_headers = {
            "User-Agent": "JARVIS-OS/0.1 (+personal assistant; read-only)",
            "Accept": "text/html,text/plain,application/json,application/rss+xml,application/xml;q=0.9",
            **(headers or {}),
        }
        for _ in range(redirects + 1):
            request = urllib.request.Request(current, headers=request_headers)
            try:
                response = self._opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise ValueError("La redirección no contiene destino.") from exc
                current = validate_public_https_url(
                    urllib.parse.urljoin(current, location)
                )
                continue
            with response:
                content_type = response.headers.get_content_type().lower()
                if not content_type.startswith(_ALLOWED_CONTENT_TYPES):
                    raise ValueError(f"Tipo de contenido no permitido: {content_type}.")
                body = response.read(self.max_bytes + 1)
                recortada = len(body) > self.max_bytes
                return current, content_type, body[: self.max_bytes], recortada
        raise ValueError("Demasiadas redirecciones.")


class SearchWebTool(Tool):
    name = "search_web"
    description = (
        "Busca información actual en Internet. Devuelve títulos, fragmentos y URLs; "
        "usa después fetch_web_page para verificar las fuentes más relevantes."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 300},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, client: WebClient, brave_api_key: str = "") -> None:
        self.client = client
        self.brave_api_key = brave_api_key

    def _search(self, query: str, count: int) -> ToolResult:
        query = query.strip()
        count = max(1, min(count, 10))
        if len(query) < 2 or len(query) > 300:
            return ToolResult(content="Consulta web inválida.", is_error=True)
        if self.brave_api_key:
            url = (
                "https://api.search.brave.com/res/v1/web/search?"
                + urllib.parse.urlencode(
                    {"q": query, "count": count, "safesearch": "moderate"}
                )
            )
            _, _, body, _ = self.client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.brave_api_key,
                },
                redirects=0,
            )
            payload = json.loads(body)
            results = payload.get("web", {}).get("results", [])[:count]
            lines = [
                f"{index}. {item.get('title', 'Sin título')}\n{item.get('description', '')}\n{item.get('url', '')}"
                for index, item in enumerate(results, 1)
            ]
        else:
            url = "https://www.bing.com/search?" + urllib.parse.urlencode(
                {"q": query, "format": "rss", "count": count}
            )
            _, _, body, _ = self.client.get(url)
            root = ET.fromstring(body)
            lines = []
            for index, item in enumerate(root.findall("./channel/item")[:count], 1):
                title = html.unescape(item.findtext("title", "Sin título"))
                description = re.sub(
                    r"<[^>]+>", " ", html.unescape(item.findtext("description", ""))
                )
                description = re.sub(r"\s+", " ", description).strip()
                link = item.findtext("link", "")
                lines.append(f"{index}. {title}\n{description}\n{link}")
        if not lines:
            return ToolResult(
                content="La búsqueda no devolvió resultados.", is_error=True
            )
        return ToolResult(content="\n\n".join(lines))

    async def run(self, query: str = "", count: int = 5, **kwargs: Any) -> ToolResult:
        try:
            return await asyncio.to_thread(self._search, query, count)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(content=f"Falló la búsqueda web: {exc}", is_error=True)


class FetchWebPageTool(Tool):
    name = "fetch_web_page"
    description = (
        "Lee una página HTTPS pública y devuelve su texto con la URL final. "
        "No puede acceder a localhost, la LAN ni servicios internos."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"url": {"type": "string", "maxLength": 2000}},
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, client: WebClient, max_text_chars: int = 18000) -> None:
        self.client = client
        self.max_text_chars = max_text_chars

    def _fetch(self, url: str) -> ToolResult:
        final_url, content_type, body, recortada = self.client.get(url)
        charset = "utf-8"
        text = body.decode(charset, errors="replace")
        title = ""
        if content_type == "text/html":
            parser = _TextExtractor()
            parser.feed(text)
            title = re.sub(r"\s+", " ", parser.title).strip()
            text = parser.text()
        recortada = recortada or len(text) > self.max_text_chars
        text = text[: self.max_text_chars]
        # Decirlo importa: sin el aviso, el modelo da por completa una página que
        # solo ha leído por arriba y responde que algo «no aparece» cuando lo que
        # pasa es que no llegó a esa parte.
        aviso = "\n\n[Página larga: esto es solo el principio.]" if recortada else ""
        return ToolResult(
            content=f"FUENTE: {final_url}\nTÍTULO: {title or '(sin título)'}\n\n{text}{aviso}"
        )

    async def run(self, url: str = "", **kwargs: Any) -> ToolResult:
        try:
            return await asyncio.to_thread(self._fetch, url)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(content=f"No pude leer la página: {exc}", is_error=True)


def register_web_tools(
    registry: Any,
    *,
    timeout: float,
    max_bytes: int,
    brave_api_key: str,
) -> None:
    client = WebClient(timeout, max_bytes)
    registry.register(SearchWebTool(client, brave_api_key))
    registry.register(FetchWebPageTool(client))

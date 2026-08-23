"""Abrir cosas para mirar: una imagen, un PDF, un documento, un vídeo, una web.

El pizarrón sirve para datos —listas, tablas, código—; esto es para lo que se
mira. Son dos cosas distintas y por eso no comparten ventana: una foto que
sustituye al pizarrón se lleva por delante la tabla que estabas leyendo.

La herramienta no devuelve el archivo. Solo valida y nombra lo que hay que
abrir; el gateway traduce esos argumentos en una orden para el HUD, igual que
hace con la música y con las peticiones. Así el contenido —que puede pesar
megas— nunca entra en el historial de la conversación ni viaja al modelo en
cada turno siguiente.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult
from jarvis_core.tools.builtin.filesystem import WorkspaceGuard

#: Extensiones que cada visor sabe mostrar. Se comprueban aquí y no en el HUD
#: porque un `.exe` renombrado a `.png` debe fallar antes de que el navegador
#: intente hacer algo con él.
VIEWER_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"),
    "pdf": (".pdf",),
    "document": (
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
        ".py", ".js", ".ts", ".html", ".css", ".sh", ".sql", ".log", ".ini",
        ".toml", ".xml", ".conf",
    ),
}

VIEWER_KINDS = ("image", "pdf", "document", "video", "web")

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"}


def youtube_video_id(raw: str) -> str:
    """Saca el identificador de un vídeo de YouTube, o cadena vacía.

    Se devuelve el identificador y no la URL a propósito: el HUD reconstruye la
    dirección del iframe a partir de él. Si dejáramos pasar la URL entera, una
    cadena que empiece por youtube.com podría arrastrar detrás otro destino.
    """
    value = (raw or "").strip()
    if _YOUTUBE_ID.match(value):
        return value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _YOUTUBE_HOSTS:
        return ""
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/")
    else:
        query = urllib.parse.parse_qs(parsed.query)
        candidate = (query.get("v") or [""])[0]
        if not candidate and parsed.path.startswith("/embed/"):
            candidate = parsed.path[len("/embed/"):]
        if not candidate and parsed.path.startswith("/shorts/"):
            candidate = parsed.path[len("/shorts/"):]
    candidate = candidate.split("/")[0].split("?")[0]
    return candidate if _YOUTUBE_ID.match(candidate) else ""


def resolve_kind(kind: str, url: str) -> str:
    """Corrige el visor cuando la URL dice claramente otra cosa.

    El caso real: se pidió `kind='web'` para un enlace de YouTube. Es una
    elección razonable —es una página— y el resultado era absurdo: intentar
    leerla como texto, tragarse un mega de HTML de la portada de YouTube y no
    enseñar el vídeo. Un enlace a un vídeo es un vídeo, lo llame como lo llame
    quien lo pide.

    Se corrige aquí y no en el prompt porque un prompt es una sugerencia y esto
    es una equivalencia: no hay ningún caso en el que abrir un watch?v= de
    YouTube como página sea lo que alguien quería.
    """
    if kind == "web" and youtube_video_id(url):
        return "video"
    return kind


class OpenViewerTool(Tool):
    name = "open_viewer"
    description = (
        "Abre una ventana flotante en el HUD para MIRAR algo: una imagen, un PDF, un "
        "documento del workspace, un vídeo de YouTube o una página web. "
        "Úsalo cuando el usuario pida ver, abrir, enseñar o poner algo. "
        "No es el pizarrón: el pizarrón (show_in_workspace) es para datos —listas, "
        "tablas, código—; esto es para contenido que se mira. "
        "Las ventanas conviven, se arrastran y se cierran a mano, así que puedes abrir "
        "varias y seguir hablando. "
        "En el chat no describas lo que ya se está viendo: di solo lo que aporta —qué es, "
        "por qué eso y no otra cosa, qué mirar en ello—."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(VIEWER_KINDS),
                "description": (
                    "'image', 'pdf' y 'document' abren un archivo del workspace por 'path'. "
                    "'video' abre YouTube por 'url' —cualquier enlace de YouTube va aquí, "
                    "no en 'web'—. 'web' NO enseña la página: solo saca su texto, porque "
                    "casi ningún sitio se deja incrustar. Para VER un sitio usa `browse`."
                ),
            },
            "path": {
                "type": "string",
                "maxLength": 400,
                "description": "Ruta dentro del workspace, para image/pdf/document.",
            },
            "url": {
                "type": "string",
                "maxLength": 2000,
                "description": "URL pública, para video (YouTube) y web.",
            },
            "title": {
                "type": "string",
                "maxLength": 100,
                "description": "Título de la ventana. Breve y descriptivo.",
            },
            "caption": {
                "type": "string",
                "maxLength": 300,
                "description": (
                    "Una línea sobre por qué se abre esto. No repitas lo que ya dirás "
                    "en el chat."
                ),
            },
        },
        "required": ["kind"],
        "additionalProperties": False,
    }

    def __init__(self, guard: WorkspaceGuard, *, browser_available: bool = False) -> None:
        self.guard = guard
        self.browser_available = browser_available

    async def run(
        self,
        kind: str = "",
        path: str = "",
        url: str = "",
        title: str = "",
        caption: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        del caption, kwargs
        kind = resolve_kind((kind or "").strip().lower(), url)
        if kind not in VIEWER_KINDS:
            return ToolResult(
                content=f"Tipo de visor inválido. Usa uno de: {', '.join(VIEWER_KINDS)}.",
                is_error=True,
            )

        if kind == "video":
            if not youtube_video_id(url):
                return ToolResult(
                    content=(
                        "Eso no es un vídeo de YouTube que pueda abrir. Necesito la URL "
                        "del vídeo o su identificador de 11 caracteres."
                    ),
                    is_error=True,
                )
            return ToolResult(content=f"Vídeo abierto en el HUD: {title or url}")

        if kind == "web":
            if self.browser_available:
                # Con navegador instalado no hay razón para enseñar una página
                # como texto: `browse` la muestra de verdad, tal como se ve. Se
                # devuelve como error con la salida a mano para que el modelo
                # reintente por el camino bueno en vez de dar esto por hecho.
                return ToolResult(
                    content=(
                        "Para ver una página usa la herramienta `browse` con "
                        f"action='open' y url='{url}': la abre en un navegador real y "
                        "se ve tal cual. El visor 'web' solo saca el texto y existe "
                        "para cuando no hay navegador."
                    ),
                    is_error=True,
                )
            # La comprobación real de destino la hace validate_public_https_url
            # en el gateway, que es quien va a salir a la red. Aquí basta con no
            # dejar pasar algo que ni siquiera es una URL.
            from jarvis_core.tools.builtin.web_tools import validate_public_https_url

            try:
                validate_public_https_url(url)
            except ValueError as exc:
                return ToolResult(content=str(exc), is_error=True)
            return ToolResult(
                content=(
                    f"Texto de la página en el HUD: {title or url}. Es solo texto, no "
                    "la página; no describas al usuario cómo se ve."
                )
            )

        try:
            resolved = self.guard.resolve(path)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if not resolved.is_file():
            return ToolResult(
                content=f"No existe el archivo {path} en el workspace.", is_error=True
            )
        permitidas = VIEWER_EXTENSIONS[kind]
        if resolved.suffix.lower() not in permitidas:
            return ToolResult(
                content=(
                    f"El visor '{kind}' no admite {resolved.suffix or 'archivos sin extensión'}. "
                    f"Admite: {', '.join(permitidas)}."
                ),
                is_error=True,
            )
        return ToolResult(
            content=f"{kind.capitalize()} abierto en el HUD: {self.guard.display(resolved)}"
        )


def register_viewer_tool(
    registry: Any, *, root: str, max_file_bytes: int, browser_available: bool = False
) -> None:
    registry.register(
        OpenViewerTool(
            WorkspaceGuard(root, max_file_bytes), browser_available=browser_available
        )
    )

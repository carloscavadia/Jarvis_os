"""La herramienta con la que JARVIS conduce el navegador.

Un paso por llamada, y cada paso devuelve lo mismo: dónde está, qué pone y qué
puede pulsar, numerado. Esa lista numerada es lo que hace que funcione: pedirle
al modelo un selector CSS que se inventa falla casi siempre y no deja ver por
qué; pedirle «el 3» es lo que acaba de leer.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from jarvis_core.browser.session import BrowserSession, BrowserUnavailable
from jarvis_core.tools.base import Tool, ToolResult

BROWSER_ACTIONS = ("open", "search", "click", "type", "scroll", "back", "read", "close")


class BrowseTool(Tool):
    name = "browse"
    description = (
        "Conduce un navegador real, paso a paso, y lo enseña en una ventana del HUD. "
        "Úsalo cuando haga falta ver o usar un sitio de verdad: buscar algo y entrar en "
        "un resultado, pasar páginas, o leer algo que fetch_web_page devuelve vacío "
        "porque la página se monta con JavaScript. "
        "Para leer un artículo cuya URL ya tienes, fetch_web_page es más rápido y basta. "
        "Acciones: 'search' busca; 'open' abre una URL; 'click' pulsa un enlace por su "
        "número de la lista anterior; 'type' escribe en el campo de la página y pulsa Enter; "
        "'scroll' baja; 'back' vuelve; 'read' relee; 'close' cierra el navegador. "
        "Cada paso devuelve la dirección, el texto y los enlaces numerados: encadena "
        "pasos hasta tener la respuesta, y cuenta lo que encontraste, no lo que pulsaste."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(BROWSER_ACTIONS)},
            "url": {"type": "string", "maxLength": 2000, "description": "Para 'open'. HTTPS público."},
            "query": {"type": "string", "maxLength": 200, "description": "Para 'search'."},
            "index": {
                "type": "integer",
                "minimum": 1,
                "description": "Para 'click': el número del enlace en la lista del paso anterior.",
            },
            "text": {"type": "string", "maxLength": 500, "description": "Para 'type'."},
            "selector": {
                "type": "string",
                "maxLength": 200,
                "description": "Para 'type', si hay que precisar el campo. Normalmente sobra.",
            },
            "amount": {"type": "integer", "description": "Para 'scroll': píxeles. Por defecto 700."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def run(
        self,
        action: str = "",
        url: str = "",
        query: str = "",
        index: int = 0,
        text: str = "",
        selector: str = "",
        amount: int = 700,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        action = (action or "").strip().lower()
        if action not in BROWSER_ACTIONS:
            return ToolResult(
                content=f"Acción inválida. Usa una de: {', '.join(BROWSER_ACTIONS)}.",
                is_error=True,
            )
        if action == "close":
            await self.session.close()
            return ToolResult(content="Navegador cerrado.")

        try:
            if action == "open":
                vista = await self.session.open(url)
            elif action == "search":
                vista = await self.session.search(query)
            elif action == "click":
                if index < 1:
                    return ToolResult(
                        content="Para pulsar hace falta el número del enlace.", is_error=True
                    )
                vista = await self.session.click_link(index)
            elif action == "type":
                vista = await self.session.type_text(text, selector)
            elif action == "scroll":
                vista = await self.session.scroll(amount)
            elif action == "back":
                vista = await self.session.back()
            else:
                vista = await self.session.read()
        except BrowserUnavailable as exc:
            return ToolResult(content=str(exc), is_error=True)
        except ValueError as exc:
            # Destino rechazado por la validación pública: es información útil,
            # no un fallo del sistema.
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            return ToolResult(
                content=f"El navegador no pudo completar el paso: {exc}", is_error=True
            )

        cuerpo: dict[str, object] = {
            "url": vista.url,
            "title": vista.title,
            "text": vista.text,
            "links": vista.links,
            # Le dice al gateway que hay captura nueva que enseñar en el HUD.
            "screenshot_version": self.session.screenshot_version,
        }
        if vista.note:
            cuerpo["note"] = vista.note
        return ToolResult(content=json.dumps(cuerpo, ensure_ascii=False))


def register_browser_tool(registry: Any, session: BrowserSession) -> None:
    registry.register(BrowseTool(session))

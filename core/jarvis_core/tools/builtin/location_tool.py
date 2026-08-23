"""«¿Dónde estoy?» — con la procedencia por delante.

Lo que devuelve nunca es solo un par de coordenadas: lleva de dónde salieron y
cuánto hace. La diferencia entre «tu móvil, hace dos minutos» y «la dirección de
casa del .env» cambia por completo lo que tiene sentido hacer con el dato, y sin
decirlo JARVIS afirmaría dónde estás cuando en realidad está adivinando.
"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from jarvis_core.location.resolver import LocationResolver
from jarvis_core.tools.base import Tool, ToolResult


class WhereAmITool(Tool):
    name = "where_am_i"
    description = (
        "Dónde está el usuario ahora mismo, según Home Assistant (la app Companion "
        "publica la posición del móvil). Úsalo antes de calcular cómo llegar a un "
        "sitio, de estimar un tiempo de viaje o de responder algo que dependa de "
        "dónde está. "
        "Devuelve también DE DÓNDE salió el dato y cuánto hace: si viene de la "
        "configuración y no del móvil, o si está caducado, dilo en la respuesta en "
        "vez de afirmar dónde está."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, resolver: LocationResolver) -> None:
        self.resolver = resolver

    async def run(self, **kwargs: Any) -> ToolResult:
        del kwargs
        resolucion = await self.resolver.resolve()
        if resolucion.place is None:
            # No es un fallo del sistema: es que falta algo concreto y se dice
            # cuál, para que JARVIS pueda pedirlo en vez de rendirse.
            return ToolResult(content=resolucion.note, is_error=True)
        cuerpo = resolucion.place.as_dict(time.time())
        if resolucion.note:
            cuerpo["warning"] = resolucion.note
        return ToolResult(content=json.dumps(cuerpo, ensure_ascii=False))


def register_location_tool(registry: Any, resolver: LocationResolver) -> None:
    registry.register(WhereAmITool(resolver))

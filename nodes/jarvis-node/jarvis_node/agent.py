"""El despachador del nodo: convierte una petición en una respuesta.

Deliberadamente no sabe nada de MQTT. Recibe el JSON crudo y devuelve el JSON crudo,
de modo que se puede probar entero sin broker, sin red y sin esperas — y de modo que
cambiar de transporte mañana no toca la lógica de ejecución.
"""

from __future__ import annotations

import logging

from jarvis_node.capabilities import CapabilityError, NodeCapabilities
from jarvis_node.operations import OPERATIONS
from jarvis_node.protocol import ProtocolError, Request, Response

logger = logging.getLogger("jarvis.node")


class NodeAgent:
    def __init__(self, capabilities: NodeCapabilities) -> None:
        self.capabilities = capabilities

    def manifest_json(self) -> str:
        import json

        return json.dumps(self.capabilities.manifest(), ensure_ascii=False)

    async def handle_raw(self, payload: str | bytes) -> str | None:
        """Procesa un mensaje entrante y devuelve la respuesta serializada.

        Devuelve `None` si el mensaje ni siquiera es una petición válida: sin `id` no
        hay a quién contestar, y publicar una respuesta sin destinatario ensucia el
        topic y confunde al gateway, que la casaría con otra petición en vuelo.
        """
        try:
            request = Request.from_json(payload)
        except ProtocolError:
            logger.warning("Petición descartada: no cumple el contrato", exc_info=True)
            return None
        return (await self.handle(request)).to_json()

    async def handle(self, request: Request) -> Response:
        operacion = OPERATIONS.get(request.op)
        if operacion is None:
            return Response.failure(request.id, f"Operación desconocida: '{request.op}'.")
        try:
            # El permiso se comprueba dos veces —aquí y dentro de cada operación—
            # porque son cosas distintas: esto niega lo que el nodo no ofrece; la
            # operación niega el argumento concreto (una ruta fuera de raíz, un
            # ejecutable no listado).
            self.capabilities.check_operation(request.op)
            resultado = await operacion(self.capabilities, **request.params)
        except CapabilityError as exc:
            logger.info("Denegado %s: %s", request.op, exc)
            return Response.denied(request.id, str(exc))
        except TypeError as exc:
            # Parámetros que no encajan con la firma: es un error del llamante, no
            # una excepción interna que merezca un stack trace.
            return Response.failure(request.id, f"Parámetros inválidos: {exc}")
        except Exception as exc:
            logger.exception("Fallo ejecutando %s", request.op)
            return Response.failure(request.id, f"{type(exc).__name__}: {exc}")
        return Response.success(request.id, resultado)

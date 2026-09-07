"""Registro de nodos: qué máquinas hay, qué ofrecen y cómo pedirles cosas.

Es deliberadamente **agnóstico del transporte**. Recibe una función para publicar y
se le van entregando los mensajes que llegan; quién los mueve —MQTT hoy, otra cosa
mañana— no es asunto suyo. Así se prueba entero sin broker, sin red y sin esperas.

La correlación petición/respuesta va por `id` con un futuro por petición. Sin eso, dos
peticiones en vuelo al mismo nodo se pisarían: la primera respuesta que llegara se
atribuiría a la petición equivocada, y el modelo recibiría el resultado de otra cosa
sin ninguna señal de que algo fue mal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("jarvis.nodes")

#: Publica (topic, payload). La implementación real la pone el gateway.
PublishFn = Callable[[str, str], Any]

DEFAULT_TIMEOUT = 30.0


class NodeError(RuntimeError):
    """No se pudo completar una operación en un nodo."""


@dataclass
class NodeInfo:
    """Lo que sabemos de una máquina, según lo que ella misma anunció."""

    node_id: str
    operations: list[str] = field(default_factory=list)
    read_roots: list[str] = field(default_factory=list)
    write_roots: list[str] = field(default_factory=list)
    executables: list[str] = field(default_factory=list)
    allow_writes: bool = False
    online: bool = False
    last_seen: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "online": self.online,
            "operations": self.operations,
            "read_roots": self.read_roots,
            "write_roots": self.write_roots,
            "executables": self.executables,
            "allow_writes": self.allow_writes,
        }


class NodeRegistry:
    def __init__(
        self,
        publish: PublishFn | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._publish = publish
        self._timeout = timeout
        self._now = now
        self._nodes: dict[str, NodeInfo] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def set_publish(self, publish: PublishFn) -> None:
        self._publish = publish

    # -- lo que llega de los nodos ---------------------------------------

    def on_manifest(self, node_id: str, payload: str | bytes) -> None:
        """Un nodo anunció lo que ofrece.

        El manifiesto es la única fuente sobre las capacidades de una máquina: aquí
        se guarda tal cual llegó, sin completarlo ni corregirlo. Inventar un valor
        que el nodo no declaró haría que el gateway prometiera algo que la máquina
        va a rechazar.
        """
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Manifiesto ilegible de '%s'", node_id)
            return
        if not isinstance(data, dict):
            return
        info = self._nodes.setdefault(node_id, NodeInfo(node_id=node_id))
        info.operations = [str(o) for o in data.get("operations", [])]
        info.read_roots = [str(p) for p in data.get("read_roots", [])]
        info.write_roots = [str(p) for p in data.get("write_roots", [])]
        info.executables = [str(e) for e in data.get("executables", [])]
        info.allow_writes = bool(data.get("allow_writes", False))
        info.last_seen = self._now()
        logger.info("Nodo '%s' anunció %d operaciones", node_id, len(info.operations))

    def on_status(self, node_id: str, payload: str | bytes) -> None:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return
        info = self._nodes.setdefault(node_id, NodeInfo(node_id=node_id))
        info.online = bool(data.get("online", False))
        info.last_seen = self._now()

    def on_response(self, payload: str | bytes) -> None:
        """Casa una respuesta con quien la esperaba."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Respuesta de nodo ilegible")
            return
        if not isinstance(data, dict):
            return
        future = self._pending.pop(str(data.get("id", "")), None)
        if future is None:
            # Llega tarde (ya venció) o no es nuestra. Descartarla es lo correcto:
            # atribuirla a otra petición sería peor que perderla.
            return
        if not future.done():
            future.set_result(data)

    # -- consulta ---------------------------------------------------------

    def nodes(self) -> list[NodeInfo]:
        return sorted(self._nodes.values(), key=lambda n: n.node_id)

    def get(self, node_id: str) -> NodeInfo | None:
        return self._nodes.get(node_id)

    # -- petición ---------------------------------------------------------

    async def call(
        self, node_id: str, op: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Pide una operación y espera su respuesta."""
        if self._publish is None:
            raise NodeError("No hay transporte configurado para hablar con los nodos.")
        info = self._nodes.get(node_id)
        if info is None:
            conocidos = ", ".join(n.node_id for n in self.nodes()) or "ninguno"
            raise NodeError(f"No conozco el nodo '{node_id}'. Conocidos: {conocidos}.")
        if not info.online:
            raise NodeError(f"El nodo '{node_id}' está desconectado.")
        # Se comprueba aquí, contra el manifiesto, para no hacer viajar por la red una
        # petición que la máquina va a rechazar de todas formas. El nodo la revalida:
        # esto es cortesía, no seguridad.
        if op not in info.operations:
            raise NodeError(
                f"El nodo '{node_id}' no ofrece '{op}'. Ofrece: {', '.join(info.operations)}."
            )

        from jarvis_core.nodes.protocol import build_request, topic_request

        request_id, payload = build_request(op, params or {})
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._publish(topic_request(node_id), payload)
            data = await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            raise NodeError(
                f"El nodo '{node_id}' no respondió en {self._timeout:.0f}s."
            ) from None
        finally:
            # Si venció, hay que soltar el futuro o la tabla crece sin fin con
            # peticiones que nadie va a contestar.
            self._pending.pop(request_id, None)

        if not data.get("ok"):
            motivo = data.get("denied_reason") or data.get("error") or "sin detalle"
            raise NodeError(motivo)
        return data.get("result")

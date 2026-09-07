"""Contrato entre el gateway y un nodo.

Un nodo es una máquina (tu Windows, el portátil, un servidor) que ejecuta cosas por
encargo de JARVIS. Arquitectónicamente es *otro dispositivo*: reutiliza el mismo
transporte MQTT que los ESP32, con su propio espacio de topics.

    jarvis/node/{node_id}/manifest  → el nodo publica qué sabe y qué le dejan hacer
    jarvis/node/{node_id}/request   ← el gateway pide una operación
    jarvis/node/{node_id}/response  → el nodo contesta
    jarvis/node/{node_id}/status    → latido

**El nodo solo abre conexiones hacia afuera.** No escucha en ningún puerto: se conecta
al broker y espera. Un PC de escritorio detrás de un router doméstico no necesita
redirección de puertos, y una máquina que no escucha no tiene superficie de entrada.

Las operaciones son **tipadas**, no cadenas de shell. `run_command` recibe `argv` como
lista, así que no hay intérprete que pueda encadenar nada: es la misma garantía que da
`create_subprocess_exec` en el núcleo, pero desde el otro extremo del cable.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1


def topic_manifest(node_id: str) -> str:
    return f"jarvis/node/{node_id}/manifest"


def topic_request(node_id: str) -> str:
    return f"jarvis/node/{node_id}/request"


def topic_response(node_id: str) -> str:
    return f"jarvis/node/{node_id}/response"


def topic_status(node_id: str) -> str:
    return f"jarvis/node/{node_id}/status"


class ProtocolError(ValueError):
    """Un mensaje que no cumple el contrato."""


@dataclass(frozen=True)
class Request:
    """Una operación pedida por el gateway."""

    id: str
    op: str
    params: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(op: str, **params: Any) -> Request:
        return Request(id=uuid.uuid4().hex, op=op, params=params)

    @staticmethod
    def from_json(raw: str | bytes) -> Request:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProtocolError(f"Payload no es JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ProtocolError("Payload no es un objeto.")
        op = data.get("op")
        if not isinstance(op, str) or not op:
            raise ProtocolError("Falta 'op'.")
        # Sin `id` no hay forma de casar la respuesta con quien la pidió, y una
        # respuesta huérfana es peor que un error: el gateway la atribuiría a otra
        # petición en vuelo.
        req_id = data.get("id")
        if not isinstance(req_id, str) or not req_id:
            raise ProtocolError("Falta 'id'.")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ProtocolError("'params' debe ser un objeto.")
        return Request(id=req_id, op=op, params=params)

    def to_json(self) -> str:
        return json.dumps(
            {"v": PROTOCOL_VERSION, "id": self.id, "op": self.op, "params": self.params},
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class Response:
    """El resultado de una operación."""

    id: str
    ok: bool
    result: Any = None
    error: str = ""
    #: Por qué se rechazó, cuando el nodo dice que no. Se separa del mensaje para que
    #: el gateway pueda distinguir "no te dejo" de "lo intenté y falló": lo primero es
    #: configuración del nodo, lo segundo es un problema de verdad.
    denied_reason: str = ""

    @staticmethod
    def success(request_id: str, result: Any) -> Response:
        return Response(id=request_id, ok=True, result=result)

    @staticmethod
    def failure(request_id: str, error: str) -> Response:
        return Response(id=request_id, ok=False, error=error)

    @staticmethod
    def denied(request_id: str, reason: str) -> Response:
        return Response(
            id=request_id, ok=False, error="Operación no permitida en este nodo.",
            denied_reason=reason,
        )

    def to_json(self) -> str:
        payload: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": self.id, "ok": self.ok}
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error
            if self.denied_reason:
                payload["denied_reason"] = self.denied_reason
        return json.dumps(payload, ensure_ascii=False, default=str)

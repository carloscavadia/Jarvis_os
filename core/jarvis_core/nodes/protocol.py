"""La mitad del contrato de nodos que vive en el cerebro.

Duplica a propósito los nombres de topic y la forma del mensaje de
`nodes/jarvis-node/jarvis_node/protocol.py`: son dos programas que se despliegan por
separado —el nodo va en tu PC, el cerebro en el servidor— y hacer que uno importe del
otro los ataría a actualizarse a la vez. El contrato es el JSON, no el módulo.
"""

from __future__ import annotations

import json
import uuid
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


def build_request(op: str, params: dict[str, Any]) -> tuple[str, str]:
    """Devuelve `(id, payload)` para una petición nueva."""
    request_id = uuid.uuid4().hex
    payload = json.dumps(
        {"v": PROTOCOL_VERSION, "id": request_id, "op": op, "params": params},
        ensure_ascii=False,
        default=str,
    )
    return request_id, payload

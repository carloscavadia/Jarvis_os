"""Herramientas para que JARVIS actúe en otras máquinas.

Son la cara visible de `jarvis-node`. Dos capacidades:

- `list_nodes` — qué máquinas hay y qué ofrece cada una. De solo lectura.
- `node_operation` — pedir una operación concreta a una máquina concreta.

`node_operation` **requiere confirmación**, y su alcance de concesión es
`nodo:operación`: aprobar "leer archivos en pc-carlos durante esta conversación" no
abre "escribir en pc-carlos", ni "leer en el servidor". Es el grano en el que un
humano puede razonar sobre lo que está concediendo.

La contención de fondo no está aquí: está en el nodo, que revalida cada petición
contra su propio manifiesto. Esto es la capa del cerebro, y por diseño es la que
menos autoridad tiene.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis_core.nodes.registry import NodeError, NodeRegistry
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult


class ListNodesTool(Tool):
    name = "list_nodes"
    description = (
        "Lista las máquinas (nodos) donde JARVIS puede ejecutar operaciones, con lo "
        "que ofrece cada una: operaciones disponibles, carpetas accesibles y programas "
        "permitidos. Úsala antes de node_operation para saber qué es posible pedir."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, registry: NodeRegistry) -> None:
        self._nodes = registry

    async def run(self, **kwargs: Any) -> ToolResult:
        nodos = [n.summary() for n in self._nodes.nodes()]
        if not nodos:
            return ToolResult(
                content=(
                    "No hay ningún nodo conectado. Para añadir una máquina, instala "
                    "jarvis-node en ella y apúntala a este broker."
                )
            )
        return ToolResult(content=json.dumps(nodos, ensure_ascii=False, indent=1))


class NodeOperationTool(Tool):
    name = "node_operation"
    requires_confirmation = True
    #: El alcance de una concesión de sesión. Ver el módulo de políticas: aprobar una
    #: vez concede `nodo:operación`, no el nodo entero ni la herramienta entera.
    policy_scope_key = "target"

    description = (
        "Ejecuta una operación en una máquina (nodo). Consulta antes list_nodes para "
        "saber qué operaciones admite cada nodo. Operaciones habituales: system_info "
        "(sin parámetros), list_directory y read_file (path), write_file (path, "
        "content), run_command (argv como lista de cadenas, nunca una línea de shell), "
        "list_processes (filter opcional)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "node_id": {"type": "string", "description": "Identificador del nodo."},
            "operation": {"type": "string", "description": "Operación a ejecutar."},
            "params": {
                "type": "object",
                "description": "Parámetros de la operación. Para run_command, argv es una lista.",
                "additionalProperties": True,
            },
        },
        "required": ["node_id", "operation"],
        "additionalProperties": False,
    }

    def __init__(self, registry: NodeRegistry) -> None:
        self._nodes = registry

    def policy_subject(self, arguments: dict[str, Any]) -> dict[str, Any]:
        subject = dict(arguments)
        node_id = str(arguments.get("node_id", ""))
        operation = str(arguments.get("operation", ""))
        subject["target"] = f"{node_id}:{operation}" if node_id and operation else ""
        return subject

    async def run(
        self,
        node_id: str = "",
        operation: str = "",
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        if not node_id or not operation:
            return ToolResult(content="Faltan 'node_id' u 'operation'.", is_error=True)
        try:
            resultado = await self._nodes.call(node_id, operation, params or {})
        except NodeError as exc:
            # El motivo del nodo vuelve tal cual: si dijo "fuera de las raíces
            # permitidas", el modelo puede corregir el tiro en vez de reintentar a
            # ciegas la misma ruta.
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=json.dumps(resultado, ensure_ascii=False, default=str))


def register_node_tools(registry: ToolRegistry, nodes: NodeRegistry) -> None:
    registry.register(ListNodesTool(nodes))
    registry.register(NodeOperationTool(nodes))

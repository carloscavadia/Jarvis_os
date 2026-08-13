"""Herramientas para diagnosticar y autorreparar con aprobación y rollback."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from jarvis_core.repairs.store import RepairStore
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult


class InspectRepairTargetTool(Tool):
    name = "inspect_repair_target"
    description = (
        "Lee un archivo dentro de la raíz explícita de autorreparación y devuelve contenido, "
        "tamaño y hash. Úsalo para diagnosticar antes de proponer cualquier modificación."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, store: RepairStore) -> None:
        self.store = store

    async def run(self, path: str = "", **kwargs: Any) -> ToolResult:
        del kwargs
        try:
            return ToolResult(
                json.dumps(self.store.inspect(path), ensure_ascii=False, indent=2)
            )
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)


class ProposeSelfRepairTool(Tool):
    name = "propose_self_repair"
    description = (
        "Prepara y valida en staging el contenido completo de reemplazo para un archivo "
        "diagnosticado. No modifica el objetivo. Usa show_in_workspace para enseñar la "
        "propuesta y luego apply_self_repair solo si el usuario quiere instalarla."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "rationale": {"type": "string", "maxLength": 2000},
            "validator": {
                "type": "string",
                "enum": ["python", "json", "toml", "text"],
            },
        },
        "required": ["path", "content", "rationale", "validator"],
        "additionalProperties": False,
    }

    def __init__(self, store: RepairStore) -> None:
        self.store = store

    async def run(
        self,
        path: str = "",
        content: str = "",
        rationale: str = "",
        validator: str = "text",
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        try:
            proposal = self.store.propose(path, content, rationale, validator)
            return ToolResult(json.dumps(proposal, ensure_ascii=False, indent=2))
        except (ValueError, SyntaxError, json.JSONDecodeError) as exc:
            return ToolResult(f"Propuesta inválida: {exc}", is_error=True)


class ListSelfRepairsTool(Tool):
    name = "list_self_repairs"
    description = "Lista propuestas de autorreparación, validaciones y estado actual."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        "additionalProperties": False,
    }

    def __init__(self, store: RepairStore) -> None:
        self.store = store

    async def run(self, limit: int = 20, **kwargs: Any) -> ToolResult:
        del kwargs
        return ToolResult(json.dumps(self.store.list(limit), ensure_ascii=False, indent=2))


class ApplySelfRepairTool(Tool):
    name = "apply_self_repair"
    description = (
        "Aplica atómicamente una propuesta validada, conservando el respaldo. Siempre "
        "requiere aprobación humana y falla si el archivo cambió desde el diagnóstico."
    )
    requires_confirmation = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"proposal_id": {"type": "string"}},
        "required": ["proposal_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: RepairStore) -> None:
        self.store = store

    async def run(self, proposal_id: str = "", **kwargs: Any) -> ToolResult:
        del kwargs
        try:
            result = self.store.apply(proposal_id)
            return ToolResult(
                "Reparación aplicada. Requiere la verificación operativa correspondiente.\n"
                + json.dumps(result, ensure_ascii=False, indent=2)
            )
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)


class RollbackSelfRepairTool(Tool):
    name = "rollback_self_repair"
    description = "Restaura el respaldo de una reparación aplicada. Requiere aprobación humana."
    requires_confirmation = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"proposal_id": {"type": "string"}},
        "required": ["proposal_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: RepairStore) -> None:
        self.store = store

    async def run(self, proposal_id: str = "", **kwargs: Any) -> ToolResult:
        del kwargs
        try:
            result = self.store.rollback(proposal_id)
            return ToolResult(
                "Rollback aplicado.\n"
                + json.dumps(result, ensure_ascii=False, indent=2)
            )
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)


def register_self_repair_tools(registry: ToolRegistry, store: RepairStore) -> None:
    registry.register(InspectRepairTargetTool(store))
    registry.register(ProposeSelfRepairTool(store))
    registry.register(ListSelfRepairsTool(store))
    registry.register(ApplySelfRepairTool(store))
    registry.register(RollbackSelfRepairTool(store))

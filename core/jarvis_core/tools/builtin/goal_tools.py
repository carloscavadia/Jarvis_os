"""Herramientas para planificar y verificar objetivos persistentes."""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis_core.goals.store import GoalStore
from jarvis_core.tools.base import Tool, ToolResult

MAX_STEPS = 12


class CreateGoalTool(Tool):
    name = "create_goal_plan"
    description = (
        "Crea un objetivo activo con un plan verificable. Úsalo para solicitudes con varios "
        "pasos o acciones; no para preguntas simples. Solo puede existir uno activo."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "description": {"type": "string", "minLength": 1, "maxLength": 1000},
            "steps": {
                "type": "array",
                "minItems": 2,
                "maxItems": MAX_STEPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "verification": {"type": "string", "maxLength": 500},
                    },
                    "required": ["title", "verification"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "description", "steps"],
        "additionalProperties": False,
    }

    def __init__(self, store: GoalStore) -> None:
        self.store = store

    async def run(
        self,
        title: str = "",
        description: str = "",
        steps: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        clean_steps = steps or []
        if not 2 <= len(clean_steps) <= MAX_STEPS:
            return ToolResult(
                f"El plan requiere entre 2 y {MAX_STEPS} pasos.", is_error=True
            )
        try:
            goal_id = self.store.create(title.strip(), description.strip(), clean_steps)
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        goal = self.store.get(goal_id)
        assert goal is not None
        return ToolResult(self.store.serialize(goal))


class GetGoalTool(Tool):
    name = "get_goal_plan"
    description = (
        "Consulta el objetivo activo o uno concreto y el avance de todos sus pasos."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"goal_id": {"type": "integer", "minimum": 1}},
        "additionalProperties": False,
    }

    def __init__(self, store: GoalStore) -> None:
        self.store = store

    async def run(self, goal_id: int | None = None, **kwargs: Any) -> ToolResult:
        del kwargs
        goal = self.store.get(goal_id)
        return ToolResult(
            self.store.serialize(goal) if goal else "No hay un objetivo activo.",
            is_error=goal is None and goal_id is not None,
        )


class UpdateGoalStepTool(Tool):
    name = "update_goal_step"
    description = (
        "Actualiza un paso del objetivo. Marca completed solo tras verificar el resultado y "
        "adjunta evidencia concreta; usa failed si la acción falló."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "goal_id": {"type": "integer", "minimum": 1},
            "position": {"type": "integer", "minimum": 1, "maximum": MAX_STEPS},
            "status": {
                "type": "string",
                "enum": ["running", "completed", "failed", "skipped"],
            },
            "evidence": {"type": "string", "maxLength": 2000},
        },
        "required": ["goal_id", "position", "status", "evidence"],
        "additionalProperties": False,
    }

    def __init__(self, store: GoalStore) -> None:
        self.store = store

    async def run(
        self,
        goal_id: int = 0,
        position: int = 0,
        status: str = "",
        evidence: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        if status == "completed" and not evidence.strip():
            return ToolResult("Un paso completado requiere evidencia.", is_error=True)
        try:
            goal = self.store.update_step(goal_id, position, status, evidence.strip())
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(self.store.serialize(goal))


class CloseGoalTool(Tool):
    name = "close_goal_plan"
    description = "Completa, cancela o bloquea el objetivo activo. Completar exige todos los pasos verificados."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "goal_id": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": ["completed", "cancelled", "blocked"]},
        },
        "required": ["goal_id", "status"],
        "additionalProperties": False,
    }

    def __init__(self, store: GoalStore) -> None:
        self.store = store

    async def run(
        self, goal_id: int = 0, status: str = "", **kwargs: Any
    ) -> ToolResult:
        del kwargs
        try:
            goal = self.store.set_status(goal_id, status)
        except ValueError as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(self.store.serialize(goal))


def register_goal_tools(registry, store: GoalStore) -> None:
    registry.register(CreateGoalTool(store))
    registry.register(GetGoalTool(store))
    registry.register(UpdateGoalStepTool(store))
    registry.register(CloseGoalTool(store))

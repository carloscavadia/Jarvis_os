"""Herramientas de tareas: permiten a JARVIS programar acciones futuras (proactividad).

Con estas, JARVIS puede responder a "avísame mañana a las 8" o "cada mañana dame el estado
del servidor" creando tareas que el scheduler ejecutará por su cuenta.
"""

from __future__ import annotations

import datetime
import math
import time
from typing import Any

from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import Tool, ToolResult

MIN_REPEAT_SECONDS = 60.0
MAX_TEXT_CHARS = 4000


def _resolve_next_run(delay_seconds: float | None, at: str | None) -> float:
    """Calcula el epoch de la primera ejecución a partir de delay o de un ISO datetime."""
    if at:
        # Acepta ISO 8601, con o sin zona horaria; si no la trae, se asume hora local.
        dt = datetime.datetime.fromisoformat(at)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.timestamp()
    if delay_seconds is not None:
        return time.time() + float(delay_seconds)
    # Sin momento indicado: dentro de un minuto por defecto.
    return time.time() + 60


class ScheduleTaskTool(Tool):
    name = "schedule_task"
    description = (
        "Programa una tarea para que JARVIS la ejecute por su cuenta en el futuro "
        "(recordatorio único o acción recurrente). El 'prompt' es la instrucción que "
        "JARVIS seguirá cuando llegue el momento (p.ej. 'recuérdale al jefe la reunión' o "
        "'dame el resumen del estado del sistema'). Indica CUÁNDO con 'at' (fecha/hora "
        "ISO 8601, p.ej. 2026-08-13T08:00:00) o con 'delay_seconds'. Para algo recurrente, "
        "añade 'repeat_seconds' (86400 = cada día). Usa antes system_info para saber la "
        "hora actual si necesitas calcular una hora concreta."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "description": "Nombre corto de la tarea.",
            },
            "prompt": {
                "type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS,
                "description": "Instrucción a ejecutar cuando toque.",
            },
            "at": {"type": "string", "description": "Fecha/hora ISO 8601 de la primera ejecución."},
            "delay_seconds": {
                "type": "number", "minimum": 0,
                "description": "Segundos desde ahora hasta la ejecución.",
            },
            "repeat_seconds": {
                "type": "number", "minimum": MIN_REPEAT_SECONDS,
                "description": "Si se repite, cada cuántos segundos (mínimo 60).",
            },
        },
        "required": ["title", "prompt"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def run(
        self,
        title: str = "",
        prompt: str = "",
        at: str | None = None,
        delay_seconds: float | None = None,
        repeat_seconds: float | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        title = title.strip()
        prompt = prompt.strip()
        if not title or not prompt:
            return ToolResult(content="Faltan 'title' o 'prompt'.", is_error=True)
        if len(title) > 200 or len(prompt) > MAX_TEXT_CHARS:
            return ToolResult(content="La tarea supera el tamaño permitido.", is_error=True)
        if delay_seconds is not None and (
            not math.isfinite(delay_seconds) or delay_seconds < 0
        ):
            return ToolResult(content="'delay_seconds' debe ser finito y no negativo.", is_error=True)
        if repeat_seconds is not None and (
            not math.isfinite(repeat_seconds) or repeat_seconds < MIN_REPEAT_SECONDS
        ):
            return ToolResult(
                content=f"'repeat_seconds' debe ser finito y al menos {MIN_REPEAT_SECONDS:g}.",
                is_error=True,
            )
        try:
            next_run = _resolve_next_run(delay_seconds, at)
        except (TypeError, ValueError, OverflowError) as exc:
            return ToolResult(content=f"Fecha/hora inválida: {exc}", is_error=True)
        if not math.isfinite(next_run) or next_run < time.time() - 1:
            return ToolResult(content="La primera ejecución no puede estar en el pasado.", is_error=True)

        task_id = self._store.add(
            title=title, prompt=prompt, next_run=next_run,
            interval_seconds=repeat_seconds,
        )
        when = datetime.datetime.fromtimestamp(next_run).astimezone().isoformat(timespec="minutes")
        recur = f", cada {repeat_seconds:g}s" if repeat_seconds else ""
        return ToolResult(content=f"Tarea #{task_id} '{title}' programada para {when}{recur}.")


class ListTasksTool(Tool):
    name = "list_tasks"
    description = "Lista las tareas programadas pendientes de JARVIS."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def run(self, **kwargs: Any) -> ToolResult:
        tasks = self._store.list()
        if not tasks:
            return ToolResult(content="No hay tareas programadas.")
        lines = []
        for t in tasks:
            when = datetime.datetime.fromtimestamp(t.next_run).astimezone().isoformat(timespec="minutes")
            recur = f" (cada {t.interval_seconds:g}s)" if t.interval_seconds else ""
            lines.append(f"#{t.id} '{t.title}' → {when}{recur}")
        return ToolResult(content="\n".join(lines))


class CancelTaskTool(Tool):
    name = "cancel_task"
    description = "Cancela una tarea programada por su id."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": "Id de la tarea a cancelar."}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def run(self, task_id: int = 0, **kwargs: Any) -> ToolResult:
        if self._store.cancel(task_id):
            return ToolResult(content=f"Tarea #{task_id} cancelada.")
        return ToolResult(content=f"No existe la tarea #{task_id}.", is_error=True)

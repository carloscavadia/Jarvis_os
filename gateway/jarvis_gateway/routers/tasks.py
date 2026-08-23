"""Endpoints de tareas programadas.

El HUD gestiona los recordatorios desde aquí: crearlos, pausarlos, reprogramarlos
y ver qué pasó en cada ejecución. Quien las dispara es el scheduler del gateway,
no el navegador —esa es la razón de que un recordatorio de madrugada funcione con
el HUD cerrado—, así que estas rutas solo tocan el almacén.

Los servicios se leen del módulo `runtime` y no se importan por nombre: un
`from ... import sessions` congela la referencia en el import y sustituirla
después, como hacen los tests, no tendría efecto aquí.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from jarvis_core.tools.builtin.task_tools import DUE_REMINDER_LEAD_SECONDS

from jarvis_gateway import runtime
from jarvis_gateway.runtime import require_api_key

logger = logging.getLogger("jarvis.gateway.tasks")
router = APIRouter(tags=["tasks"])


class TaskControlRequest(BaseModel):
    action: str = Field(pattern=r"^(pause|resume|reschedule)$")
    next_run: float | None = None
    interval_seconds: float | None = None


class TaskCreateRequest(BaseModel):
    title: str
    prompt: str
    next_run: float | None = None
    interval_seconds: float | None = None
    #: Cuándo vence el entregable, si lo hay. Distinto de `next_run`, que es
    #: cuándo actúa JARVIS. Sin este campo, el botón «+ TAREA» del HUD no podía
    #: crear nada con plazo y la sinergia con el calendario quedaba solo del
    #: lado del agente.
    due_at: float | None = None


@router.get("/tasks", dependencies=[Depends(require_api_key)])
async def list_tasks(include_disabled: bool = True):
    tasks = runtime.sessions.tasks.list(include_disabled=include_disabled)
    ahora = time.time()
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "kind": t.kind,
                "next_run": t.next_run,
                "interval_seconds": t.interval_seconds,
                "enabled": t.enabled,
                "created_at": t.created_at,
                "last_run": t.last_run,
                "status": t.status,
                "priority": t.priority,
                "category": t.category,
                "runs": t.runs,
                "failures": t.failures,
                "attempts": t.attempts,
                "last_error": t.last_error,
                "last_result": t.last_result,
                "due_at": t.due_at,
                "overdue": t.is_overdue(ahora),
                "time_precision": t.time_precision,
            }
            for t in tasks
        ],
        "stats": runtime.sessions.tasks.stats(),
    }


@router.post("/tasks", dependencies=[Depends(require_api_key)])
async def create_task(req: TaskCreateRequest):
    # El POST se saltaba las comprobaciones que sí hace la herramienta del
    # agente: aceptaba un momento en el pasado —que se dispara al instante— o un
    # intervalo de un segundo, que convierte al scheduler en un bucle cerrado.
    next_run = req.next_run if req.next_run else (time.time() + 60)
    if not math.isfinite(next_run) or next_run < time.time() - 1:
        raise HTTPException(
            status_code=422, detail="La ejecución no puede estar en el pasado."
        )
    interval = req.interval_seconds
    if interval is not None and (not math.isfinite(interval) or interval < 60):
        raise HTTPException(
            status_code=422, detail="La repetición mínima es de 60 segundos."
        )
    due_at = req.due_at
    if due_at is not None and not math.isfinite(due_at):
        raise HTTPException(status_code=422, detail="La fecha de entrega no es válida.")
    # Con plazo y sin momento de actuar, el aviso se pone solo la víspera: es la
    # misma regla que aplica la herramienta del agente, para que crear la tarea
    # desde el HUD o pidiéndoselo a JARVIS dé el mismo resultado.
    if due_at is not None and not req.next_run:
        next_run = max(due_at - DUE_REMINDER_LEAD_SECONDS, time.time() + 60)
    task_id = runtime.sessions.tasks.add(
        title=req.title,
        prompt=req.prompt,
        next_run=next_run,
        interval_seconds=interval,
        due_at=due_at,
    )
    return {"status": "ok", "task_id": task_id, "message": f"Tarea creada con ID {task_id}"}


@router.post("/tasks/{task_id}/control", dependencies=[Depends(require_api_key)])
async def control_task(task_id: int, req: TaskControlRequest):
    """Pausa, reanuda o reprograma sin tener que borrar y volver a crear."""
    if req.action == "pause":
        ok = runtime.sessions.tasks.pause(task_id)
    elif req.action == "resume":
        ok = runtime.sessions.tasks.resume(task_id)
    else:
        if req.next_run is not None and (
            not math.isfinite(req.next_run) or req.next_run < time.time() - 1
        ):
            raise HTTPException(
                status_code=422, detail="La ejecución no puede estar en el pasado."
            )
        ok = runtime.sessions.tasks.reschedule(
            task_id, req.next_run, req.interval_seconds
        ) is not None
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="La tarea no existe o no admite esa acción en su estado actual.",
        )
    task = runtime.sessions.tasks.get(task_id)
    return {"status": "ok", "task": {"id": task.id, "status": task.status,
                                     "next_run": task.next_run}}


@router.post("/tasks/{task_id}/complete", dependencies=[Depends(require_api_key)])
async def complete_task(task_id: int):
    """Marca un entregable como hecho.

    Aparte de cancelar a propósito: cancelar dice que ya no va a hacerse, y esto
    dice que está hecho. Con el mismo botón para las dos cosas, el historial no
    distinguiría lo que se entregó de lo que se abandonó.
    """
    task = runtime.sessions.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="La tarea no existe.")
    runtime.sessions.tasks.complete(task_id)
    return {"status": "ok", "task": {"id": task_id, "status": "done"}}


@router.get("/tasks/history", dependencies=[Depends(require_api_key)])
async def task_history(task_id: int | None = None, limit: int = 50):
    """Qué pasó de verdad en cada ejecución, no solo cuándo tocó."""
    return {
        "runs": [
            {
                "id": run.id, "task_id": run.task_id, "title": run.title,
                "started_at": run.started_at, "finished_at": run.finished_at,
                "ok": run.ok, "detail": run.detail,
            }
            for run in runtime.sessions.tasks.history(task_id, limit)
        ]
    }


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_api_key)])
async def delete_task(task_id: int):
    success = runtime.sessions.tasks.cancel(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="La tarea no existe o ya fue cancelada.")
    return {"status": "ok", "message": f"Tarea {task_id} cancelada"}

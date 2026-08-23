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
#: Con cuánta antelación se avisa de un entregable cuando no se dijo otra cosa.
#: Un día: lo bastante pronto para poder hacer algo, lo bastante tarde para que
#: el aviso siga siendo sobre esto y no una nota que se olvida.
DUE_REMINDER_LEAD_SECONDS = 86400.0


def resolve_zone(name: str = "") -> datetime.tzinfo | None:
    """Zona horaria configurada, o la del sistema si no hay ninguna.

    Dentro de un contenedor la del sistema es UTC salvo que se declare TZ, así
    que «mañana a las 8» acababa sonando a las 10 en España. Declararla explícita
    es lo que hace que la hora que dice el usuario y la que entiende JARVIS sean
    la misma.
    """
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — nombre inválido: se cae a la del sistema.
        return None


def _resolve_next_run(
    delay_seconds: float | None, at: str | None, zone: datetime.tzinfo | None = None
) -> float | None:
    """Epoch de la primera ejecución, o None si no se indicó ninguno."""
    if at:
        dt = datetime.datetime.fromisoformat(at)
        if dt.tzinfo is None:
            # Sin zona explícita, la hora es la del usuario, no la del proceso.
            dt = dt.replace(tzinfo=zone) if zone else dt.astimezone()
        return dt.timestamp()
    if delay_seconds is not None:
        return time.time() + float(delay_seconds)
    # Sin momento indicado no hay hora que devolver. Antes esto valía «dentro de
    # un minuto», que no es lo que nadie quiso decir jamás: quien no da fecha no
    # está pidiendo un aviso inmediato. Quien llame decide qué hacer con la
    # ausencia; `schedule_task` la convierte en un borrador y pregunta.
    return None


def format_when(epoch: float, zone: datetime.tzinfo | None = None) -> str:
    moment = datetime.datetime.fromtimestamp(epoch, tz=zone) if zone else \
        datetime.datetime.fromtimestamp(epoch).astimezone()
    return moment.isoformat(timespec="minutes")


def humanize_interval(seconds: float | None) -> str:
    if not seconds:
        return ""
    for size, unit in ((86400, "día"), (3600, "hora"), (60, "minuto")):
        if seconds >= size and seconds % size == 0:
            count = int(seconds // size)
            plural = "s" if count != 1 else ""
            return f"cada {count} {unit}{plural}" if count > 1 else f"cada {unit}"
    return f"cada {seconds:g}s"


class ScheduleTaskTool(Tool):
    name = "schedule_task"
    description = (
        "Programa una tarea para que JARVIS la ejecute por su cuenta en el futuro "
        "(recordatorio único o acción recurrente). El 'prompt' es la instrucción que "
        "JARVIS seguirá cuando llegue el momento (p.ej. 'recuérdale al jefe la reunión' o "
        "'dame el resumen del estado del sistema'). Indica CUÁNDO con 'at' (fecha/hora "
        "ISO 8601, p.ej. 2026-08-13T08:00:00) o con 'delay_seconds'. Para algo recurrente, "
        "añade 'repeat_seconds' (86400 = cada día). Usa antes system_info para saber la "
        "hora actual si necesitas calcular una hora concreta.\n\n"
        "NUNCA te inventes la fecha ni la hora. Si el usuario no las ha dicho, llama "
        "igualmente sin 'at' ni 'delay_seconds': se anota como borrador, no avisa de "
        "nada, y entonces le preguntas cuándo es. Decir «te lo he agendado el lunes 24 "
        "a las 17:00» cuando nadie ha dicho ni el día ni la hora es peor que no "
        "apuntarlo: el usuario se queda creyendo que hay una cita a esa hora."
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
            "priority": {
                "type": "string", "enum": ["low", "normal", "high"],
                "description": "Prioridad; las altas se ejecutan antes si coinciden.",
            },
            "category": {
                "type": "string", "maxLength": 64,
                "description": "Etiqueta para agrupar (casa, trabajo, salud…).",
            },
            "due_at": {
                "type": "string",
                "description": (
                    "Fecha/hora ISO 8601 en que VENCE el entregable. Es distinto de 'at': "
                    "'at' es cuándo actúas tú, 'due_at' es cuándo tiene que estar hecho. "
                    "Si el usuario dice «para el viernes», eso es 'due_at'. Ponlo siempre "
                    "que haya un plazo: aparece en el calendario, avisa la víspera y, si "
                    "pasa sin cerrarse, queda marcado como vencido."
                ),
            },
        },
        "required": ["title", "prompt"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(
        self,
        title: str = "",
        prompt: str = "",
        at: str | None = None,
        delay_seconds: float | None = None,
        repeat_seconds: float | None = None,
        priority: str = "normal",
        category: str = "",
        due_at: str | None = None,
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
        vence: float | None = None
        if due_at:
            try:
                vence = _resolve_next_run(None, due_at, self._zone)
            except (TypeError, ValueError, OverflowError) as exc:
                return ToolResult(content=f"Fecha de entrega inválida: {exc}", is_error=True)
            if not math.isfinite(vence):
                return ToolResult(content="Fecha de entrega inválida.", is_error=True)

        try:
            next_run = _resolve_next_run(delay_seconds, at, self._zone)
        except (TypeError, ValueError, OverflowError) as exc:
            return ToolResult(content=f"Fecha/hora inválida: {exc}", is_error=True)

        # Con plazo y sin momento de actuar, el aviso se pone solo la víspera.
        # Es lo que hace que anotar «vence el viernes» sirva de algo sin tener
        # que calcular a mano cuándo recordarlo; y si el plazo es más inmediato
        # que la antelación, el aviso se pega al plazo en vez de irse al pasado.
        if vence is not None and next_run is None:
            next_run = max(vence - DUE_REMINDER_LEAD_SECONDS, time.time() + 60)

        # Nadie dijo cuándo. Se anota como BORRADOR y se pide la fecha, en vez de
        # inventarse una. Es la diferencia entre «lo tengo apuntado, ¿qué día
        # era?» y «tienes una cita el lunes 24 a las 17:00» cuando nadie ha dicho
        # ni el día ni la hora.
        if next_run is None:
            task_id = self._store.add(
                title=title, prompt=prompt, next_run=time.time(),
                interval_seconds=repeat_seconds, priority=priority,
                category=category, due_at=None, status="draft",
            )
            return ToolResult(
                content=(
                    f"Tarea #{task_id} '{title}' anotada SIN FECHA, como borrador. "
                    "No va a avisar de nada hasta que tenga una. "
                    "Pregúntale al usuario qué día y a qué hora es, dile que la has "
                    "anotado mientras tanto, y NO des por buena ninguna fecha que no "
                    "te haya dicho él. Cuando te la dé, usa reschedule_task."
                )
            )

        if not math.isfinite(next_run) or next_run < time.time() - 1:
            return ToolResult(content="La primera ejecución no puede estar en el pasado.", is_error=True)

        task_id = self._store.add(
            title=title, prompt=prompt, next_run=next_run,
            interval_seconds=repeat_seconds, priority=priority, category=category,
            due_at=vence,
        )
        when = format_when(next_run, self._zone)
        recur = f", {humanize_interval(repeat_seconds)}" if repeat_seconds else ""
        plazo = f" Vence el {format_when(vence, self._zone)}." if vence is not None else ""
        return ToolResult(
            content=f"Tarea #{task_id} '{title}' programada para {when}{recur}.{plazo}"
        )


class ListTasksTool(Tool):
    name = "list_tasks"
    description = (
        "Lista las tareas de JARVIS con su estado, cuándo toca y si alguna falló. "
        "Con include_finished también muestra las ya cumplidas y canceladas."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_finished": {
                "type": "boolean",
                "description": "Incluir cumplidas, canceladas y fallidas.",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(self, include_finished: bool = False, **kwargs: Any) -> ToolResult:
        del kwargs
        tasks = self._store.list(include_disabled=bool(include_finished))
        if not tasks:
            return ToolResult(content="No hay tareas programadas.")
        ahora = time.time()
        lines = []
        for t in tasks:
            when = format_when(t.next_run, self._zone)
            recur = f" · {humanize_interval(t.interval_seconds)}" if t.interval_seconds else ""
            flags = []
            if t.status != "pending":
                flags.append(t.status)
            if t.priority != "normal":
                flags.append(t.priority)
            if t.attempts:
                flags.append(f"reintento {t.attempts}")
            # Un error anterior es lo primero que hay que saber de una tarea:
            # sin él, «programada» y «lleva tres días fallando» se ven igual.
            if t.last_error:
                flags.append(f"último fallo: {t.last_error[:80]}")
            # El plazo va delante de todo lo demás: entre «se ejecuta el martes»
            # y «venció hace dos días», lo segundo es lo que hay que ver primero.
            if t.is_overdue(ahora):
                flags.insert(0, f"VENCIDA el {format_when(t.due_at, self._zone)}")
            elif t.due_at is not None:
                flags.insert(0, f"vence {format_when(t.due_at, self._zone)}")
            suffix = f"  [{' · '.join(flags)}]" if flags else ""
            if t.status == "draft":
                # Un borrador no tiene hora: enseñar la de creación como si la
                # tuviera es justo el equívoco que el borrador viene a evitar.
                lines.append(
                    f"#{t.id} '{t.title}' → SIN FECHA (borrador: pregúntale al "
                    f"usuario cuándo es y usa reschedule_task)"
                )
                continue
            lines.append(f"#{t.id} '{t.title}' → {when}{recur}{suffix}")
        return ToolResult(content="\n".join(lines))


class CompleteTaskTool(Tool):
    name = "complete_task"
    description = (
        "Marca un entregable como HECHO. Úsalo cuando el usuario diga que ya lo "
        "entregó, lo terminó o lo resolvió. No es lo mismo que cancel_task: cancelar "
        "dice que ya no va a hacerse, y esto dice que está hecho."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": "Id de la tarea."}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(self, task_id: int = 0, **kwargs: Any) -> ToolResult:
        del kwargs
        tarea = self._store.get(int(task_id))
        if tarea is None:
            return ToolResult(content=f"No existe la tarea #{task_id}.", is_error=True)
        self._store.complete(int(task_id))
        return ToolResult(content=f"Tarea #{task_id} '{tarea.title}' marcada como hecha.")


class CancelTaskTool(Tool):
    name = "cancel_task"
    description = "Cancela una tarea programada por su id."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": "Id de la tarea a cancelar."}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(self, task_id: int = 0, **kwargs: Any) -> ToolResult:
        if self._store.cancel(task_id):
            return ToolResult(content=f"Tarea #{task_id} cancelada.")
        return ToolResult(content=f"No existe la tarea #{task_id}.", is_error=True)


class _TaskIdTool(Tool):
    """Base de las herramientas que actúan sobre una tarea concreta."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "integer", "description": "Id de la tarea."}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone


class PauseTaskTool(_TaskIdTool):
    name = "pause_task"
    description = (
        "Suspende una tarea sin borrarla: deja de ejecutarse pero conserva su "
        "programación. Úsala cuando el usuario quiera silenciar algo "
        "temporalmente, no cuando quiera eliminarlo (para eso, cancel_task)."
    )

    async def run(self, task_id: int = 0, **kwargs: Any) -> ToolResult:
        del kwargs
        if self._store.pause(task_id):
            return ToolResult(content=f"Tarea #{task_id} en pausa.")
        return ToolResult(
            content=f"No pude pausar #{task_id}: no existe o no está pendiente.",
            is_error=True,
        )


class ResumeTaskTool(_TaskIdTool):
    name = "resume_task"
    description = (
        "Reanuda una tarea en pausa. Si su momento ya pasó mientras estaba "
        "suspendida, se coloca en la siguiente ocurrencia en vez de dispararse "
        "de golpe."
    )

    async def run(self, task_id: int = 0, **kwargs: Any) -> ToolResult:
        del kwargs
        if not self._store.resume(task_id):
            return ToolResult(
                content=f"No pude reanudar #{task_id}: no existe o no está en pausa.",
                is_error=True,
            )
        task = self._store.get(task_id)
        when = format_when(task.next_run, self._zone) if task else "?"
        return ToolResult(content=f"Tarea #{task_id} reanudada para {when}.")


class RescheduleTaskTool(Tool):
    name = "reschedule_task"
    description = (
        "Cambia cuándo se ejecuta una tarea ya creada, o cada cuánto se repite. "
        "Úsala cuando el usuario quiera mover un recordatorio en vez de crear "
        "otro, para no acabar con duplicados."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Id de la tarea."},
            "at": {"type": "string", "description": "Nueva fecha/hora ISO 8601."},
            "delay_seconds": {
                "type": "number", "minimum": 0,
                "description": "Segundos desde ahora hasta la nueva ejecución.",
            },
            "repeat_seconds": {
                "type": "number", "minimum": MIN_REPEAT_SECONDS,
                "description": "Nuevo intervalo de repetición.",
            },
            "stop_repeating": {
                "type": "boolean",
                "description": "Convertir una tarea recurrente en única.",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(
        self,
        task_id: int = 0,
        at: str | None = None,
        delay_seconds: float | None = None,
        repeat_seconds: float | None = None,
        stop_repeating: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        next_run: float | None = None
        if at or delay_seconds is not None:
            try:
                next_run = _resolve_next_run(delay_seconds, at, self._zone)
            except (TypeError, ValueError, OverflowError) as exc:
                return ToolResult(content=f"Fecha/hora inválida: {exc}", is_error=True)
            if not math.isfinite(next_run) or next_run < time.time() - 1:
                return ToolResult(
                    content="La nueva ejecución no puede estar en el pasado.",
                    is_error=True,
                )
        if repeat_seconds is not None and (
            not math.isfinite(repeat_seconds) or repeat_seconds < MIN_REPEAT_SECONDS
        ):
            return ToolResult(
                content=f"'repeat_seconds' debe ser al menos {MIN_REPEAT_SECONDS:g}.",
                is_error=True,
            )
        task = self._store.reschedule(
            task_id, next_run, repeat_seconds, clear_interval=bool(stop_repeating)
        )
        if task is None:
            return ToolResult(
                content=f"No pude reprogramar #{task_id}: no existe o ya terminó.",
                is_error=True,
            )
        recur = f", {humanize_interval(task.interval_seconds)}" if task.interval_seconds else ""
        return ToolResult(
            content=f"Tarea #{task_id} reprogramada para "
                    f"{format_when(task.next_run, self._zone)}{recur}."
        )


class TaskHistoryTool(Tool):
    name = "task_history"
    description = (
        "Muestra qué pasó realmente en las últimas ejecuciones de las tareas: si "
        "se cumplieron, qué respondieron o por qué fallaron. Úsala cuando el "
        "usuario pregunte si algo se ejecutó, o para diagnosticar una tarea que "
        "no está funcionando."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Limitar a una tarea."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore, zone: datetime.tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone

    async def run(
        self, task_id: int | None = None, limit: int = 10, **kwargs: Any
    ) -> ToolResult:
        del kwargs
        runs = self._store.history(task_id, limit)
        if not runs:
            return ToolResult(content="Todavía no se ha ejecutado ninguna tarea.")
        lines = []
        for run in runs:
            mark = "…" if run.ok is None else ("✓" if run.ok else "✗")
            when = format_when(run.started_at, self._zone)
            lines.append(f"{mark} #{run.task_id} '{run.title}' {when} — {run.detail[:160]}")
        return ToolResult(content="\n".join(lines))

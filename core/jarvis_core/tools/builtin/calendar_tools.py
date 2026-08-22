"""Herramientas de agenda: crear, consultar, mover y borrar eventos."""

from __future__ import annotations

import datetime
import time
from typing import Any, ClassVar

from jarvis_core.calendar.store import CalendarEvent, CalendarStore
from jarvis_core.tools.base import Tool, ToolResult, ToolRegistry
from jarvis_core.tools.builtin.task_tools import resolve_zone

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _parse_moment(texto: str, zone: datetime.tzinfo | None) -> float:
    """ISO 8601 a epoch. Sin zona explícita, la hora es la del usuario.

    Es la misma regla que las tareas: dentro del contenedor el reloj es UTC, y
    tomar esa por buena hacía que «a las 8» acabara siendo las 10 en España.
    """
    momento = datetime.datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=zone) if zone else momento.astimezone()
    return momento.timestamp()


def _format(evento: CalendarEvent, zone: datetime.tzinfo | None) -> str:
    def local(epoch: float) -> datetime.datetime:
        return (
            datetime.datetime.fromtimestamp(epoch, tz=zone)
            if zone
            else datetime.datetime.fromtimestamp(epoch).astimezone()
        )

    inicio, fin = local(evento.starts_at), local(evento.ends_at)
    dia = f"{_DIAS[inicio.weekday()]} {inicio.day:02d}/{inicio.month:02d}"
    if evento.all_day:
        cuando = f"{dia}, todo el día"
    elif inicio.date() == fin.date():
        cuando = f"{dia} {inicio:%H:%M}–{fin:%H:%M}"
    else:
        cuando = f"{dia} {inicio:%H:%M} → {fin:%d/%m %H:%M}"
    partes = [f"[{evento.id}] {evento.title} · {cuando}"]
    if evento.location:
        partes.append(f"en {evento.location}")
    if evento.description:
        partes.append(f"— {evento.description}")
    return " ".join(partes)


class CreateEventTool(Tool):
    name = "create_event"
    description = (
        "Crea un evento en la agenda del usuario. Para algo que JARVIS deba "
        "ejecutar a una hora (un recordatorio, un cron), usa las herramientas de "
        "tareas: la agenda es para lo que ocurre, no para lo que se ejecuta."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Título breve del evento."},
            "starts_at": {
                "type": "string",
                "description": "Inicio en ISO 8601, p. ej. 2026-08-25T17:30. Sin zona se asume la del usuario.",
            },
            "ends_at": {"type": "string", "description": "Fin en ISO 8601 (opcional; por defecto una hora)."},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "all_day": {"type": "boolean"},
        },
        "required": ["title", "starts_at"],
        "additionalProperties": False,
    }

    def __init__(self, store: CalendarStore, zone: datetime.tzinfo | None = None) -> None:
        self.store = store
        self.zone = zone

    async def run(
        self,
        title: str = "",
        starts_at: str = "",
        ends_at: str = "",
        description: str = "",
        location: str = "",
        all_day: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            inicio = _parse_moment(starts_at, self.zone)
            fin = _parse_moment(ends_at, self.zone) if ends_at else None
            evento = self.store.add(title, inicio, fin, description, location, all_day)
        except ValueError as exc:
            return ToolResult(f"No pude crear el evento: {exc}", is_error=True)
        return ToolResult(f"Evento creado. {_format(evento, self.zone)}")


class ListEventsTool(Tool):
    name = "list_events"
    description = (
        "Consulta la agenda. Sin argumentos devuelve lo que viene a continuación; "
        "con `days` mira esa ventana de días desde hoy."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Días hacia delante desde hoy. 1 = solo hoy, 7 = esta semana.",
            },
            "query": {"type": "string", "description": "Filtra por texto en título, lugar o notas."},
            "limit": {"type": "integer"},
        },
        "additionalProperties": False,
    }

    def __init__(self, store: CalendarStore, zone: datetime.tzinfo | None = None) -> None:
        self.store = store
        self.zone = zone

    async def run(
        self, days: int = 0, query: str = "", limit: int = 20, **kwargs: Any
    ) -> ToolResult:
        if query:
            eventos = self.store.search(query, limit)
        elif days > 0:
            ahora = time.time()
            # Desde el arranque del día local, no desde este instante: si son las
            # 18:00 y preguntas «qué tengo hoy», lo de las 10:00 sigue contando.
            hoy = (
                datetime.datetime.now(tz=self.zone)
                if self.zone
                else datetime.datetime.now().astimezone()
            ).replace(hour=0, minute=0, second=0, microsecond=0)
            eventos = self.store.range(hoy.timestamp(), ahora + days * 86400, limit)
        else:
            eventos = self.store.upcoming(limit=limit)

        if not eventos:
            return ToolResult("No hay nada en la agenda para esa consulta.")
        return ToolResult("\n".join(_format(e, self.zone) for e in eventos))


class UpdateEventTool(Tool):
    name = "update_event"
    description = "Cambia un evento existente: mover la hora, renombrarlo o cambiar el lugar."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer"},
            "title": {"type": "string"},
            "starts_at": {"type": "string", "description": "Nuevo inicio en ISO 8601."},
            "ends_at": {"type": "string", "description": "Nuevo fin en ISO 8601."},
            "description": {"type": "string"},
            "location": {"type": "string"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: CalendarStore, zone: datetime.tzinfo | None = None) -> None:
        self.store = store
        self.zone = zone

    async def run(self, event_id: int = 0, **kwargs: Any) -> ToolResult:
        cambios: dict[str, object] = {}
        for campo in ("title", "description", "location"):
            if kwargs.get(campo):
                cambios[campo] = kwargs[campo]
        try:
            for campo in ("starts_at", "ends_at"):
                if kwargs.get(campo):
                    cambios[campo] = _parse_moment(str(kwargs[campo]), self.zone)
        except ValueError as exc:
            return ToolResult(f"Fecha inválida: {exc}", is_error=True)

        evento = self.store.update(event_id, **cambios)
        if evento is None:
            return ToolResult(f"No existe el evento {event_id}.", is_error=True)
        return ToolResult(f"Evento actualizado. {_format(evento, self.zone)}")


class DeleteEventTool(Tool):
    name = "delete_event"
    description = "Borra un evento de la agenda."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"event_id": {"type": "integer"}},
        "required": ["event_id"],
        "additionalProperties": False,
    }
    #: Borrar una cita es irreversible y el usuario puede no tenerla en otro
    #: sitio: se pide confirmación, como en el resto de acciones destructivas.
    requires_confirmation = True

    def __init__(self, store: CalendarStore) -> None:
        self.store = store

    async def run(self, event_id: int = 0, **kwargs: Any) -> ToolResult:
        if self.store.delete(event_id):
            return ToolResult(f"Evento {event_id} borrado.")
        return ToolResult(f"No existe el evento {event_id}.", is_error=True)


def register_calendar_tools(
    registry: ToolRegistry, store: CalendarStore, timezone: str = ""
) -> None:
    zone = resolve_zone(timezone)
    registry.register(CreateEventTool(store, zone))
    registry.register(ListEventsTool(store, zone))
    registry.register(UpdateEventTool(store, zone))
    registry.register(DeleteEventTool(store))

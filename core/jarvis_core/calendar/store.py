"""Agenda local respaldada por SQLite y segura entre hilos.

Deliberadamente separada de las tareas. Una tarea es algo que JARVIS **ejecuta**
por ti a una hora; un evento es algo que **ocurre** y sobre lo que quieres que
te avise o te informe. Mezclarlas obligaría a que cada cita del dentista fuese
un trabajo del scheduler, y a que cada cron apareciera en tu agenda.

Los momentos se guardan en epoch UTC. La zona del usuario se aplica solo al
mostrar: guardar la hora local hace que un cambio de `TZ` mueva citas pasadas.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

#: Duración por defecto cuando no se indica final. Una cita sin fin declarado
#: casi nunca dura todo el día, y un evento de duración cero no se ve en ninguna
#: vista de agenda.
DEFAULT_DURATION_SECONDS = 3600.0


@dataclass
class CalendarEvent:
    id: int
    title: str
    description: str
    location: str
    starts_at: float
    ends_at: float
    all_day: bool
    created_at: float


class CalendarStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    location    TEXT NOT NULL DEFAULT '',
                    starts_at   REAL NOT NULL,
                    ends_at     REAL NOT NULL,
                    all_day     INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL
                )
                """
            )
            # Las consultas de agenda son siempre por rango de fechas.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_starts ON events(starts_at)"
            )
            self._conn.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> CalendarEvent:
        return CalendarEvent(
            id=int(r["id"]),
            title=r["title"],
            description=r["description"],
            location=r["location"],
            starts_at=float(r["starts_at"]),
            ends_at=float(r["ends_at"]),
            all_day=bool(r["all_day"]),
            created_at=float(r["created_at"]),
        )

    def add(
        self,
        title: str,
        starts_at: float,
        ends_at: float | None = None,
        description: str = "",
        location: str = "",
        all_day: bool = False,
    ) -> CalendarEvent:
        title = title.strip()
        if not title:
            raise ValueError("El evento necesita un título.")
        if ends_at is None:
            ends_at = starts_at + DEFAULT_DURATION_SECONDS
        if ends_at < starts_at:
            raise ValueError("El evento no puede terminar antes de empezar.")
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO events
                    (title, description, location, starts_at, ends_at, all_day, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (title, description, location, starts_at, ends_at, int(all_day), now),
            )
        return CalendarEvent(
            id=int(cursor.lastrowid),
            title=title,
            description=description,
            location=location,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            created_at=now,
        )

    def get(self, event_id: int) -> CalendarEvent | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._row(row) if row else None

    def range(self, start: float, end: float, limit: int = 200) -> list[CalendarEvent]:
        """Eventos que **se solapan** con la ventana, no solo los que empiezan en ella.

        Una reunión de 9 a 11 tiene que salir al preguntar por las 10, y con una
        condición sobre `starts_at` no saldría.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events
                WHERE starts_at < ? AND ends_at > ?
                ORDER BY starts_at ASC LIMIT ?
                """,
                (end, start, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._row(r) for r in rows]

    def upcoming(self, now: float | None = None, limit: int = 10) -> list[CalendarEvent]:
        reference = time.time() if now is None else now
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE ends_at > ? ORDER BY starts_at ASC LIMIT ?",
                (reference, max(1, min(int(limit), 200))),
            ).fetchall()
        return [self._row(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[CalendarEvent]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events
                WHERE title LIKE ? OR description LIKE ? OR location LIKE ?
                ORDER BY starts_at DESC LIMIT ?
                """,
                (like, like, like, max(1, min(int(limit), 200))),
            ).fetchall()
        return [self._row(r) for r in rows]

    def update(self, event_id: int, **campos: object) -> CalendarEvent | None:
        permitidos = {
            "title", "description", "location", "starts_at", "ends_at", "all_day",
        }
        cambios = {k: v for k, v in campos.items() if k in permitidos and v is not None}
        if not cambios:
            return self.get(event_id)
        if "all_day" in cambios:
            cambios["all_day"] = int(bool(cambios["all_day"]))
        asignaciones = ", ".join(f"{k} = ?" for k in cambios)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE events SET {asignaciones} WHERE id = ?",
                (*cambios.values(), event_id),
            )
        evento = self.get(event_id)
        if evento and evento.ends_at < evento.starts_at:
            # Mover solo el inicio puede dejar el evento invertido; se corrige
            # conservando la duración en vez de rechazar el cambio.
            self.update(event_id, ends_at=evento.starts_at + DEFAULT_DURATION_SECONDS)
            return self.get(event_id)
        return evento

    def delete(self, event_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

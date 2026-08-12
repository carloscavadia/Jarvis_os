"""Almacén de tareas programadas (persistente en SQLite).

Una tarea es una instrucción que JARVIS debe ejecutar por su cuenta en un momento dado:
un recordatorio único ("avísame mañana a las 8") o algo recurrente ("cada mañana dame el
resumen del sistema"). Como se guardan en disco, sobreviven a reinicios del servicio.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass


@dataclass
class Task:
    id: int
    title: str
    prompt: str
    kind: str            # "once" | "recurring"
    next_run: float      # epoch (segundos)
    interval_seconds: float | None
    enabled: bool
    created_at: float
    last_run: float | None


class TaskStore:
    def __init__(self, db_path: str) -> None:
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                title            TEXT NOT NULL,
                prompt           TEXT NOT NULL,
                kind             TEXT NOT NULL,
                next_run         REAL NOT NULL,
                interval_seconds REAL,
                enabled          INTEGER NOT NULL DEFAULT 1,
                created_at       REAL NOT NULL,
                last_run         REAL
            )
            """
        )
        self._conn.commit()

    def _row_to_task(self, r: sqlite3.Row) -> Task:
        return Task(
            id=r["id"], title=r["title"], prompt=r["prompt"], kind=r["kind"],
            next_run=r["next_run"], interval_seconds=r["interval_seconds"],
            enabled=bool(r["enabled"]), created_at=r["created_at"], last_run=r["last_run"],
        )

    def add(
        self,
        title: str,
        prompt: str,
        next_run: float,
        interval_seconds: float | None = None,
    ) -> int:
        kind = "recurring" if interval_seconds else "once"
        cur = self._conn.execute(
            """
            INSERT INTO tasks (title, prompt, kind, next_run, interval_seconds, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (title, prompt, kind, next_run, interval_seconds, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list(self, include_disabled: bool = False) -> list[Task]:
        if include_disabled:
            cur = self._conn.execute("SELECT * FROM tasks ORDER BY next_run ASC")
        else:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE enabled = 1 ORDER BY next_run ASC"
            )
        return [self._row_to_task(r) for r in cur.fetchall()]

    def due(self, now: float | None = None) -> list[Task]:
        """Tareas activas cuyo momento de ejecución ya llegó."""
        now = now if now is not None else time.time()
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE enabled = 1 AND next_run <= ? ORDER BY next_run ASC",
            (now,),
        )
        return [self._row_to_task(r) for r in cur.fetchall()]

    def mark_fired(self, task: Task) -> None:
        """Marca una tarea como ejecutada; reprograma si es recurrente, desactiva si es única."""
        now = time.time()
        if task.kind == "recurring" and task.interval_seconds:
            # Siguiente ejecución evitando acumular retrasos.
            next_run = now + task.interval_seconds
            self._conn.execute(
                "UPDATE tasks SET last_run = ?, next_run = ? WHERE id = ?",
                (now, next_run, task.id),
            )
        else:
            self._conn.execute(
                "UPDATE tasks SET last_run = ?, enabled = 0 WHERE id = ?",
                (now, task.id),
            )
        self._conn.commit()

    def cancel(self, task_id: int) -> bool:
        cur = self._conn.execute("UPDATE tasks SET enabled = 0 WHERE id = ?", (task_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()

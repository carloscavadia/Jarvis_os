"""Almacén persistente y concurrente de tareas programadas.

Una tarea no es solo «qué y cuándo»: también es qué pasó cuando le tocó. Sin
ese registro, un recordatorio que falló es indistinguible de uno que se cumplió,
y el usuario se entera —si se entera— porque el aviso nunca llegó.

Estados
-------
``pending``    esperando su momento
``running``    ejecutándose ahora mismo
``done``       tarea única cumplida
``failed``     agotó los reintentos
``cancelled``  el usuario la retiró
``paused``     suspendida sin perder su programación

`enabled` se conserva porque el resto del sistema y las bases ya desplegadas lo
usan, pero es un derivado: solo ``pending`` y ``running`` están activas.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

ACTIVE_STATUSES = ("pending", "running")
#: Un borrador: se anotó, pero le falta algo para poder programarse —hoy, la
#: fecha—. Existe porque «sin fecha» tiene que poder representarse.
#:
#: Antes no se podía: si no se indicaba momento, la tarea se creaba «dentro de un
#: minuto», que no es lo que nadie quiso decir nunca. Al modelo le quedaban dos
#: salidas —preguntar, o inventarse una fecha— y se inventaba: «tienes una cita
#: el lunes 24 a las 17:00» cuando nadie había dicho ni el día ni la hora. La
#: forma de que no invente es que decir «no sé cuándo» sea una opción.
#:
#: `due()` filtra por status = 'pending', así que un borrador NUNCA se dispara.
TASK_STATUSES = (
    "pending", "running", "done", "failed", "cancelled", "paused", "draft",
)
#: Lo que sigue abierto: lo activo y lo que aún es borrador. Es lo que se lista
#: por defecto —un borrador invisible se olvidaría, que es justo lo contrario de
#: lo que se busca al anotarlo—.
OPEN_STATUSES = (*ACTIVE_STATUSES, "draft")
PRIORITIES = ("low", "normal", "high")

#: Intentos por ejecución antes de darla por fallida, y espera entre ellos.
#: Un proveedor de IA caído o un corte de red son transitorios: perder por eso
#: un «avísame de la reunión» es el peor fallo posible en un gestor de tareas.
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (60.0, 300.0)


@dataclass
class Task:
    id: int
    title: str
    prompt: str
    kind: str
    next_run: float
    interval_seconds: float | None
    enabled: bool
    created_at: float
    last_run: float | None
    status: str = "pending"
    priority: str = "normal"
    category: str = ""
    runs: int = 0
    failures: int = 0
    attempts: int = 0
    last_error: str = ""
    last_result: str = ""
    #: Cuándo vence el entregable. Es OTRA cosa que `next_run`, y por eso es un
    #: campo aparte: `next_run` es cuándo actúa JARVIS y `due_at` es cuándo tiene
    #: que estar hecho. Confundirlos era justo lo que impedía anotar un plazo:
    #: programar «el informe vence el viernes» hacía que el viernes JARVIS
    #: ejecutara algo, en vez de avisar de que vencía.
    due_at: float | None = None
    #: 'exact' si la hora la dijo el usuario; 'day' si solo dijo el día y la hora
    #: la puso el sistema. Se guarda porque cambia lo que JARVIS puede afirmar:
    #: con 'day' tiene que decir que la hora la eligió él, no darla por acordada.
    time_precision: str = "exact"
    #: Marca de que ya se avisó del vencimiento, para no repetirlo en cada vuelta
    #: del planificador. Una tarea vencida se ve en el HUD todo el tiempo; el
    #: aviso se manda una vez.
    due_notified: bool = False

    @property
    def is_recurring(self) -> bool:
        return self.kind == "recurring" and bool(self.interval_seconds)

    def is_overdue(self, now: float) -> bool:
        """Vencida: tenía plazo, pasó, y sigue sin cerrarse.

        Se calcula, no se guarda. Un estado 'overdue' almacenado necesitaría que
        algo lo fuese poniendo al día y quedaría desfasado en cuanto el
        planificador estuviera parado un rato.
        """
        return (
            self.due_at is not None
            and self.due_at < now
            and self.status in ACTIVE_STATUSES
        )


@dataclass
class TaskRun:
    id: int
    task_id: int
    title: str
    started_at: float
    finished_at: float | None
    ok: bool | None
    detail: str


class TaskStore:
    def __init__(self, db_path: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.max_attempts = max(1, int(max_attempts))
        self._init_schema()

    # ── Esquema ──────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
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
            # Las bases ya desplegadas traen solo las columnas de arriba: se
            # añaden las nuevas sin tocar los datos existentes.
            existing = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            added = {
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "priority": "TEXT NOT NULL DEFAULT 'normal'",
                "category": "TEXT NOT NULL DEFAULT ''",
                "runs": "INTEGER NOT NULL DEFAULT 0",
                "failures": "INTEGER NOT NULL DEFAULT 0",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT NOT NULL DEFAULT ''",
                "last_result": "TEXT NOT NULL DEFAULT ''",
                "due_at": "REAL",
                "due_notified": "INTEGER NOT NULL DEFAULT 0",
                "time_precision": "TEXT NOT NULL DEFAULT 'exact'",
            }
            for column, definition in added.items():
                if column not in existing:
                    self._conn.execute(
                        f"ALTER TABLE tasks ADD COLUMN {column} {definition}"
                    )
            if "status" not in existing:
                # Una tarea vieja desactivada pudo cumplirse o pudo cancelarse;
                # el esquema anterior no distinguía. Se marcan como 'done', que
                # es el caso normal, en vez de inventar un fallo que no consta.
                self._conn.execute(
                    "UPDATE tasks SET status = CASE WHEN enabled = 1 "
                    "THEN 'pending' ELSE 'done' END"
                )

            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, next_run)"
            )
            # El calendario pregunta por rango de fechas de entrega, no de
            # ejecución: sin este índice esa consulta recorre la tabla entera.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     INTEGER NOT NULL,
                    title       TEXT NOT NULL,
                    started_at  REAL NOT NULL,
                    finished_at REAL,
                    ok          INTEGER,
                    detail      TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, id)"
            )
            self._conn.commit()

    @staticmethod
    def _row_to_task(r: sqlite3.Row) -> Task:
        return Task(
            id=r["id"],
            title=r["title"],
            prompt=r["prompt"],
            kind=r["kind"],
            next_run=r["next_run"],
            interval_seconds=r["interval_seconds"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
            last_run=r["last_run"],
            status=r["status"],
            priority=r["priority"],
            category=r["category"],
            runs=r["runs"],
            failures=r["failures"],
            attempts=r["attempts"],
            last_error=r["last_error"],
            last_result=r["last_result"],
            due_at=r["due_at"],
            due_notified=bool(r["due_notified"]),
            time_precision=r["time_precision"],
        )

    # ── Alta y consulta ──────────────────────────────────────────────────────

    def add(
        self,
        title: str,
        prompt: str,
        next_run: float,
        interval_seconds: float | None = None,
        *,
        priority: str = "normal",
        category: str = "",
        due_at: float | None = None,
        status: str = "pending",
        time_precision: str = "exact",
    ) -> int:
        kind = "recurring" if interval_seconds else "once"
        if status not in TASK_STATUSES:
            status = "pending"
        if priority not in PRIORITIES:
            priority = "normal"
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO tasks
                    (title, prompt, kind, next_run, interval_seconds, enabled,
                     created_at, status, priority, category, due_at, time_precision)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (title, prompt, kind, next_run, interval_seconds, time.time(),
                 status, priority, category.strip()[:64], due_at,
                 "day" if time_precision == "day" else "exact"),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get(self, task_id: int) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list(
        self,
        include_disabled: bool = False,
        *,
        statuses: tuple[str, ...] | None = None,
        category: str = "",
    ) -> list[Task]:
        """Tareas ordenadas por cuándo toca.

        `include_disabled` se mantiene por compatibilidad: sin él se devuelven
        solo las activas, que es lo que espera el scheduler y el listado normal.
        """
        query = "SELECT * FROM tasks"
        params: list[object] = []
        wanted = statuses if statuses is not None else (
            None if include_disabled else OPEN_STATUSES
        )
        clauses = []
        if wanted:
            clauses.append(f"status IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        # Las de mayor prioridad primero cuando vencen a la vez.
        query += (
            " ORDER BY next_run ASC,"
            " CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END"
        )
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def due(self, now: float | None = None) -> list[Task]:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending' AND next_run <= ?
                ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1
                         ELSE 2 END, next_run ASC
                """,
                (now,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── Ciclo de ejecución ───────────────────────────────────────────────────

    def begin_run(self, task: Task) -> int | None:
        """Marca la tarea en ejecución y abre su registro.

        Devuelve None si otro ciclo se le adelantó: el cambio de estado es la
        reserva, así que dos pasadas del scheduler no pueden ejecutarla dos
        veces.
        """
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'running', last_run = ? "
                "WHERE id = ? AND status = 'pending'",
                (now, task.id),
            )
            if cur.rowcount == 0:
                return None
            run = self._conn.execute(
                "INSERT INTO task_runs(task_id, title, started_at) VALUES (?, ?, ?)",
                (task.id, task.title, now),
            )
            return int(run.lastrowid)

    def _next_occurrence(self, task: Task, now: float) -> float:
        """Siguiente hueco de la serie, sin arrastrar el retraso.

        Sumar el intervalo a `now` haría que una tarea de las 8:00 ejecutada con
        retraso a las 11:00 pasara a ser de las 11:00 para siempre. Avanzar
        sobre la programación original conserva la hora y se salta de una vez
        las ocurrencias perdidas mientras el servidor estuvo apagado, en vez de
        dispararlas todas de golpe.
        """
        interval = float(task.interval_seconds or 0)
        if interval <= 0:
            return now
        next_run = task.next_run
        if next_run <= now:
            missed = int((now - next_run) // interval) + 1
            next_run += missed * interval
        return next_run

    def finish_run(
        self, task: Task, run_id: int | None, ok: bool, detail: str = ""
    ) -> Task | None:
        """Cierra la ejecución y decide qué le pasa a la tarea."""
        now = time.time()
        detail = (detail or "").strip()[:2000]
        with self._lock, self._conn:
            if run_id is not None:
                self._conn.execute(
                    "UPDATE task_runs SET finished_at = ?, ok = ?, detail = ? "
                    "WHERE id = ?",
                    (now, int(ok), detail, run_id),
                )

            if ok:
                if task.is_recurring:
                    self._conn.execute(
                        "UPDATE tasks SET status='pending', enabled=1, next_run=?, "
                        "runs=runs+1, attempts=0, last_error='', last_result=? "
                        "WHERE id=?",
                        (self._next_occurrence(task, now), detail, task.id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE tasks SET status='done', enabled=0, runs=runs+1, "
                        "attempts=0, last_error='', last_result=? WHERE id=?",
                        (detail, task.id),
                    )
            else:
                attempts = task.attempts + 1
                if attempts < self.max_attempts:
                    # Reintento: sigue pendiente y vuelve a intentarlo pronto.
                    backoff = RETRY_BACKOFF_SECONDS[
                        min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                    ]
                    self._conn.execute(
                        "UPDATE tasks SET status='pending', enabled=1, next_run=?, "
                        "attempts=?, failures=failures+1, last_error=? WHERE id=?",
                        (now + backoff, attempts, detail, task.id),
                    )
                elif task.is_recurring:
                    # Una recurrente no se pierde por un mal día: se salta esta
                    # ocurrencia y espera a la siguiente.
                    self._conn.execute(
                        "UPDATE tasks SET status='pending', enabled=1, next_run=?, "
                        "attempts=0, failures=failures+1, last_error=? WHERE id=?",
                        (self._next_occurrence(task, now), detail, task.id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE tasks SET status='failed', enabled=0, attempts=?, "
                        "failures=failures+1, last_error=? WHERE id=?",
                        (attempts, detail, task.id),
                    )
        return self.get(task.id)

    def recover_running(self) -> int:
        """Devuelve a la cola lo que quedó 'running' tras un reinicio.

        Sin esto, un corte de luz en mitad de una ejecución dejaría la tarea
        marcada como en curso para siempre y nunca volvería a dispararse.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status='pending' WHERE status='running'"
            )
            self._conn.execute(
                "UPDATE task_runs SET finished_at = ?, ok = 0, "
                "detail = 'Interrumpida por un reinicio del servidor.' "
                "WHERE finished_at IS NULL",
                (time.time(),),
            )
        return cur.rowcount

    # ── Gestión ──────────────────────────────────────────────────────────────

    def _set_status(self, task_id: int, status: str, *, only_from: tuple[str, ...]) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE tasks SET status=?, enabled=? WHERE id=? "
                f"AND status IN ({','.join('?' * len(only_from))})",
                (status, int(status in ACTIVE_STATUSES), task_id, *only_from),
            )
        return cur.rowcount > 0

    def cancel(self, task_id: int) -> bool:
        return self._set_status(
            task_id, "cancelled", only_from=("pending", "running", "paused")
        )

    def pause(self, task_id: int) -> bool:
        return self._set_status(task_id, "paused", only_from=("pending",))

    def resume(self, task_id: int) -> bool:
        """Reanuda, y si se quedó atrás la coloca en su siguiente hueco."""
        task = self.get(task_id)
        if task is None or task.status != "paused":
            return False
        now = time.time()
        next_run = task.next_run
        if next_run <= now:
            next_run = self._next_occurrence(task, now) if task.is_recurring else now
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status='pending', enabled=1, next_run=? WHERE id=?",
                (next_run, task_id),
            )
        return True

    def reschedule(
        self,
        task_id: int,
        next_run: float | None = None,
        interval_seconds: float | None = None,
        *,
        clear_interval: bool = False,
    ) -> Task | None:
        task = self.get(task_id)
        if task is None or task.status in {"cancelled", "done", "failed"}:
            return None
        # Un borrador solo deja de serlo cuando llega una fecha DE VERDAD.
        # Heredar la suya —que es la de creación, un relleno— lo pondría a
        # dispararse de inmediato, que es el fallo que el borrador evita.
        if task.status == "draft" and next_run is None:
            return None
        new_next = next_run if next_run is not None else task.next_run
        if clear_interval:
            new_interval, kind = None, "once"
        elif interval_seconds is not None:
            new_interval, kind = interval_seconds, "recurring"
        else:
            new_interval, kind = task.interval_seconds, task.kind
        with self._lock, self._conn:
            self._conn.execute(
                # El borrador se promueve aquí: darle fecha es exactamente lo
                # que le faltaba. Sin esto se quedaría en borrador para siempre
                # y no se dispararía nunca, con la fecha ya puesta.
                # Dar una hora concreta convierte la precisión en exacta: a
                # partir de ahí JARVIS ya puede afirmarla sin matices.
                "UPDATE tasks SET next_run=?, interval_seconds=?, kind=?, attempts=0, "
                "time_precision='exact', "
                "status=CASE WHEN status='draft' THEN 'pending' ELSE status END, "
                "enabled=CASE WHEN status='draft' THEN 1 ELSE enabled END "
                "WHERE id=?",
                (new_next, new_interval, kind, task_id),
            )
        return self.get(task_id)

    # ── Entregables ──────────────────────────────────────────────────────────

    def due_between(self, start: float, end: float) -> list[Task]:
        """Tareas cuya ENTREGA cae en el rango. Es lo que consulta el calendario.

        Ojo con qué fecha se filtra: por `due_at`, no por `next_run`. Preguntar
        por la de ejecución devolvería el día en que JARVIS avisa, que no es el
        día en que la cosa vence, y el calendario mostraría los avisos en vez de
        los plazos.
        """
        # Lo cancelado no aparece: cancelar dice que eso ya no va a hacerse, y
        # seguir pintándolo en el calendario sería enseñar un plazo que no
        # existe. Lo cumplido sí sigue saliendo —el HUD lo tacha—, porque un día
        # pasado tiene que poder contar lo que se entregó.
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE due_at IS NOT NULL "
                "AND due_at >= ? AND due_at < ? AND status != 'cancelled' "
                "ORDER BY due_at ASC",
                (start, end),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def overdue(self, now: float, *, only_unnotified: bool = False) -> list[Task]:
        """Lo que venció y sigue abierto."""
        query = (
            "SELECT * FROM tasks WHERE due_at IS NOT NULL AND due_at < ? "
            f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
        )
        params: list[object] = [now, *ACTIVE_STATUSES]
        if only_unnotified:
            query += " AND due_notified = 0"
        query += " ORDER BY due_at ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def mark_due_notified(self, task_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET due_notified = 1 WHERE id = ?", (task_id,)
            )
            self._conn.commit()

    def complete(self, task_id: int) -> Task | None:
        """Cierra un entregable a mano.

        Hacía falta un cierre propio: 'done' solo lo ponía el planificador tras
        ejecutar, y cancelar dice otra cosa —que ya no va a hacerse—. Un
        entregable que entregas no está cancelado.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = 'done', enabled = 0 WHERE id = ?",
                (task_id,),
            )
            self._conn.commit()
        return self.get(task_id)

    def update(
        self,
        task_id: int,
        *,
        title: str | None = None,
        prompt: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        due_at: float | None = None,
        clear_due: bool = False,
    ) -> Task | None:
        fields: list[str] = []
        params: list[object] = []
        if clear_due:
            # Quitar el plazo también rearma el aviso: si más adelante se le pone
            # otra fecha, tiene que volver a avisar.
            fields.append("due_at=NULL")
            fields.append("due_notified=0")
        elif due_at is not None:
            fields.append("due_at=?"); params.append(due_at)
            fields.append("due_notified=0")
        if title is not None:
            fields.append("title=?"); params.append(title.strip()[:200])
        if prompt is not None:
            fields.append("prompt=?"); params.append(prompt.strip()[:4000])
        if priority is not None and priority in PRIORITIES:
            fields.append("priority=?"); params.append(priority)
        if category is not None:
            fields.append("category=?"); params.append(category.strip()[:64])
        if not fields:
            return self.get(task_id)
        params.append(task_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", params
            )
        return self.get(task_id)

    # ── Historial ────────────────────────────────────────────────────────────

    def history(self, task_id: int | None = None, limit: int = 50) -> list[TaskRun]:
        limit = max(1, min(int(limit), 500))
        query = "SELECT * FROM task_runs"
        params: list[object] = []
        if task_id is not None:
            query += " WHERE task_id = ?"
            params.append(task_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            TaskRun(
                id=r["id"], task_id=r["task_id"], title=r["title"],
                started_at=r["started_at"], finished_at=r["finished_at"],
                ok=None if r["ok"] is None else bool(r["ok"]),
                detail=r["detail"],
            )
            for r in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS total FROM tasks GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in TASK_STATUSES}
        for row in rows:
            counts[row["status"]] = row["total"]
        counts["active"] = counts["pending"] + counts["running"]
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()

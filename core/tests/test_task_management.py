"""Tests de la gestión de tareas.

El fallo que ordena todo lo demás: una tarea que falla no puede desaparecer. En
el esquema anterior el scheduler la desactivaba ANTES de ejecutarla, así que si
el proveedor de IA no respondía, «avísame de la reunión» se perdía sin dejar
rastro y el usuario solo se enteraba porque el aviso nunca llegó.
"""

import asyncio
import sqlite3
import time

import pytest
from jarvis_core.tasks.scheduler import Scheduler
from jarvis_core.tasks.store import TaskStore


@pytest.fixture()
def store(tmp_path):
    return TaskStore(str(tmp_path / "tareas.db"), max_attempts=2)


async def run_scheduler(store, on_fire, *, seconds=0.4):
    scheduler = Scheduler(store, on_fire=on_fire, poll_interval=0.05)
    scheduler.start()
    await asyncio.sleep(seconds)
    await scheduler.stop()


# ── Un fallo no pierde la tarea ──────────────────────────────────────────────


async def test_a_failure_keeps_the_task_and_records_why(store):
    store.add("Llamar al médico", "Recuérdaselo", next_run=time.time() - 1)

    async def falla(task):
        raise RuntimeError("el proveedor de IA no respondió")

    await run_scheduler(store, falla)

    task = store.get(1)
    assert task.status == "pending"          # sigue viva
    assert task.attempts == 1                # y va a reintentarlo
    assert "no respondió" in task.last_error
    assert task.next_run > time.time()
    history = store.history()
    assert history[0].ok is False


def test_a_one_shot_that_exhausts_its_retries_ends_as_failed(store):
    """Se recorre el ciclo a mano: `reschedule` reinicia los intentos —lo
    correcto al reprogramar— y forzar la ejecución por ahí los borraría."""
    store.add("Imposible", "…", next_run=time.time() - 1)

    for _ in range(store.max_attempts):
        task = store.get(1)
        run_id = store.begin_run(task)
        store.finish_run(task, run_id, False, "nunca funciona")

    task = store.get(1)
    assert task.status == "failed"
    assert task.failures == store.max_attempts
    # Y queda constancia de todos los intentos, no solo del último.
    assert len(store.history(1)) == store.max_attempts


def test_a_recurring_task_survives_a_bad_day(store):
    store.add("Parte diario", "…", next_run=time.time() - 1, interval_seconds=3600)

    for _ in range(store.max_attempts):
        task = store.get(1)
        run_id = store.begin_run(task)
        store.finish_run(task, run_id, False, "hoy no")

    task = store.get(1)
    # Agotados los intentos se salta esta ocurrencia, pero la serie continúa:
    # una recurrente no debe morir por un mal día.
    assert task.status == "pending"
    assert task.next_run > time.time()
    assert task.attempts == 0


async def test_a_success_records_what_the_task_answered(store):
    store.add("Parte", "Dame el estado", next_run=time.time() - 1)

    async def responde(task):
        return "Todo en orden, jefe."

    await run_scheduler(store, responde)

    task = store.get(1)
    assert task.status == "done"
    assert task.runs == 1
    assert task.last_result == "Todo en orden, jefe."
    assert store.history()[0].ok is True


async def test_a_hung_task_does_not_freeze_the_scheduler(store):
    """Sin tiempo límite, una tarea colgada detenía el bucle para siempre."""
    store.add("Se cuelga", "…", next_run=time.time() - 1)

    async def cuelga(task):
        await asyncio.sleep(30)

    scheduler = Scheduler(store, on_fire=cuelga, poll_interval=0.05, task_timeout=0.1)
    scheduler.start()
    await asyncio.sleep(0.4)
    await scheduler.stop()

    task = store.get(1)
    assert task.last_error and "límite" in task.last_error


async def test_two_passes_never_fire_the_same_task_twice(store):
    """El cambio de estado es la reserva; sin ella habría avisos duplicados."""
    store.add("Una sola vez", "…", next_run=time.time() - 1)
    fired = []

    async def lenta(task):
        fired.append(task.id)
        await asyncio.sleep(0.2)
        return "ok"

    await run_scheduler(store, lenta, seconds=0.5)
    assert fired == [1]


# ── Recurrencia sin deriva ───────────────────────────────────────────────────


def test_a_late_run_does_not_move_the_daily_hour_forever(store):
    """Sumar el intervalo a «ahora» convertía las 8:00 en las 11:00 para siempre."""
    eight = time.time() - 3 * 3600          # tocaba hace 3 horas
    store.add("Parte matinal", "…", next_run=eight, interval_seconds=86400)
    task = store.get(1)

    store.finish_run(task, None, True, "hecho")

    # La siguiente cae 24 h después de la programada, no de la ejecución real.
    assert store.get(1).next_run == pytest.approx(eight + 86400, abs=1)


def test_a_long_outage_skips_the_missed_runs_instead_of_firing_them_all(store):
    """Tras tres días apagado, un recordatorio diario no debe sonar tres veces."""
    start = time.time() - 3 * 86400 - 60
    store.add("Diario", "…", next_run=start, interval_seconds=86400)
    task = store.get(1)

    store.finish_run(task, None, True, "hecho")

    next_run = store.get(1).next_run
    assert next_run > time.time()
    assert next_run == pytest.approx(start + 4 * 86400, abs=1)


# ── Gestión ──────────────────────────────────────────────────────────────────


def test_pause_and_resume_keep_the_task_out_of_the_queue(store):
    store.add("Ruidosa", "…", next_run=time.time() + 60)

    assert store.pause(1) is True
    assert store.due(time.time() + 120) == []
    assert store.list() == []

    assert store.resume(1) is True
    assert store.get(1).status == "pending"


def test_resuming_late_does_not_fire_everything_at_once(store):
    store.add("Diaria", "…", next_run=time.time() - 86400, interval_seconds=86400)
    store.pause(1)
    store.resume(1)
    assert store.get(1).next_run > time.time()


def test_cancelling_and_completing_are_different_states(store):
    """Antes ambos eran `enabled = 0` y no había forma de distinguirlos."""
    store.add("Cancelada", "…", next_run=time.time() + 60)
    store.add("Cumplida", "…", next_run=time.time() + 60)
    store.cancel(1)
    store.finish_run(store.get(2), None, True, "hecho")

    assert store.get(1).status == "cancelled"
    assert store.get(2).status == "done"
    assert store.stats()["cancelled"] == 1
    assert store.stats()["done"] == 1


def test_high_priority_goes_first_when_two_are_due_together(store):
    moment = time.time() - 1
    store.add("Normal", "…", next_run=moment)
    store.add("Urgente", "…", next_run=moment, priority="high")
    assert [t.title for t in store.due()] == ["Urgente", "Normal"]


def test_a_restart_returns_interrupted_tasks_to_the_queue(store):
    """Sin esto, un corte a mitad de ejecución las dejaba colgadas para siempre."""
    store.add("A medias", "…", next_run=time.time() - 1)
    store.begin_run(store.get(1))
    assert store.get(1).status == "running"

    assert store.recover_running() == 1
    assert store.get(1).status == "pending"
    assert store.history()[0].ok is False


# ── Migración ────────────────────────────────────────────────────────────────


def test_an_old_database_keeps_working(tmp_path):
    """Las bases ya desplegadas traen el esquema anterior."""
    path = tmp_path / "vieja.db"
    old = sqlite3.connect(path)
    old.execute(
        """CREATE TABLE tasks (
               id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
               prompt TEXT NOT NULL, kind TEXT NOT NULL, next_run REAL NOT NULL,
               interval_seconds REAL, enabled INTEGER NOT NULL DEFAULT 1,
               created_at REAL NOT NULL, last_run REAL)"""
    )
    old.execute(
        "INSERT INTO tasks(title, prompt, kind, next_run, enabled, created_at) "
        "VALUES ('Pendiente', '…', 'once', ?, 1, ?)",
        (time.time() + 60, time.time()),
    )
    old.execute(
        "INSERT INTO tasks(title, prompt, kind, next_run, enabled, created_at) "
        "VALUES ('Antigua', '…', 'once', ?, 0, ?)",
        (time.time() - 60, time.time()),
    )
    old.commit()
    old.close()

    store = TaskStore(str(path))

    assert store.get(1).status == "pending"
    # La desactivada pudo cumplirse o cancelarse; el esquema viejo no lo decía.
    assert store.get(2).status == "done"
    assert [t.title for t in store.list()] == ["Pendiente"]
    store.close()

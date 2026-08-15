"""Scheduler: el bucle que hace a JARVIS proactivo.

Corre en segundo plano dentro del servicio 24/7. Cada pocos segundos revisa si
hay tareas que toca ejecutar y lanza cada una contra el agente.

Tres decisiones que no son obvias:

**El estado es la reserva.** Marcar la tarea como `running` antes de ejecutarla
es lo que impide que dos pasadas del bucle la disparen dos veces, y a la vez
deja el fallo registrado si algo sale mal. El esquema anterior la desactivaba
antes de ejecutarla: si el proveedor de IA fallaba, la tarea desaparecía sin
dejar rastro y el aviso simplemente no llegaba.

**Cada tarea va por su cuenta.** Una llamada al modelo tarda decenas de
segundos; encadenarlas dejaba el bucle bloqueado y retrasaba todo lo demás.

**Con tiempo límite.** Sin él, una tarea colgada detenía el scheduler para
siempre, y las demás no volvían a ejecutarse nunca.

No consume nada mientras no hay tareas vencidas: solo comprueba el reloj.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from jarvis_core.tasks.store import Task, TaskStore

logger = logging.getLogger("jarvis.scheduler")

#: Devuelve el texto del resultado, que se guarda en el historial.
OnFire = Callable[[Task], Awaitable[str | None]]

DEFAULT_TASK_TIMEOUT = 300.0
DEFAULT_MAX_CONCURRENT = 3


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        on_fire: OnFire,
        poll_interval: float = 5.0,
        *,
        task_timeout: float = DEFAULT_TASK_TIMEOUT,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._store = store
        self._on_fire = on_fire
        self._poll_interval = poll_interval
        self._task_timeout = task_timeout
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._task: asyncio.Task | None = None
        self._running: set[asyncio.Task] = set()
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        # Un corte a mitad de ejecución deja tareas marcadas como en curso que
        # nadie volvería a mirar; al arrancar se devuelven a la cola.
        recovered = self._store.recover_running()
        if recovered:
            logger.warning(
                "%s tarea(s) quedaron interrumpidas por un reinicio; vuelven a la cola",
                recovered,
            )
        self._task = asyncio.create_task(self._run(), name="jarvis-scheduler")
        logger.info("Scheduler iniciado (poll cada %.1fs).", self._poll_interval)

    async def stop(self) -> None:
        self._stopped.set()
        for pending in list(self._running):
            pending.cancel()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Deja que las ejecuciones en vuelo cierren su registro.
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                for task in self._store.due():
                    run_id = self._store.begin_run(task)
                    if run_id is None:
                        # Otra pasada se le adelantó.
                        continue
                    logger.info("Disparando tarea #%s: %s", task.id, task.title)
                    launched = asyncio.create_task(self._execute(task, run_id))
                    self._running.add(launched)
                    launched.add_done_callback(self._running.discard)
            except Exception:
                logger.exception("Error en el bucle del scheduler")
            await asyncio.sleep(self._poll_interval)

    async def _execute(self, task: Task, run_id: int) -> None:
        async with self._semaphore:
            try:
                result = await asyncio.wait_for(
                    self._on_fire(task), timeout=self._task_timeout
                )
                self._store.finish_run(task, run_id, True, str(result or "Completada."))
            except asyncio.TimeoutError:
                logger.warning(
                    "La tarea #%s superó el tiempo límite de %.0fs",
                    task.id, self._task_timeout,
                )
                self._store.finish_run(
                    task, run_id, False,
                    f"Superó el tiempo límite de {self._task_timeout:.0f}s.",
                )
            except asyncio.CancelledError:
                # Parada del servicio: no cuenta como fallo de la tarea.
                self._store.finish_run(
                    task, run_id, False, "Cancelada al detener el servicio."
                )
                raise
            except Exception as exc:
                logger.exception("Error ejecutando la tarea #%s", task.id)
                self._store.finish_run(
                    task, run_id, False, f"{type(exc).__name__}: {exc}"
                )

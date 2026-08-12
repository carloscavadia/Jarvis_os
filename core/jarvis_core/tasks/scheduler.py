"""Scheduler: el bucle que hace a JARVIS proactivo.

Corre en segundo plano dentro del servicio 24/7. Cada pocos segundos revisa si hay tareas
que toca ejecutar y, por cada una, llama a un callback (`on_fire`) que normalmente lanza al
agente y difunde el resultado a los dispositivos/usuario.

No consume nada mientras no hay tareas vencidas: solo comprueba el reloj.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from jarvis_core.tasks.store import Task, TaskStore

logger = logging.getLogger("jarvis.scheduler")

OnFire = Callable[[Task], Awaitable[None]]


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        on_fire: OnFire,
        poll_interval: float = 5.0,
    ) -> None:
        self._store = store
        self._on_fire = on_fire
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="jarvis-scheduler")
        logger.info("Scheduler iniciado (poll cada %.1fs).", self._poll_interval)

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                for task in self._store.due():
                    logger.info("Disparando tarea #%s: %s", task.id, task.title)
                    # Reprograma/desactiva ANTES de ejecutar, para no duplicar disparos.
                    self._store.mark_fired(task)
                    try:
                        await self._on_fire(task)
                    except Exception:  # noqa: BLE001
                        logger.exception("Error ejecutando la tarea #%s", task.id)
            except Exception:  # noqa: BLE001 — el bucle no debe morir nunca.
                logger.exception("Error en el bucle del scheduler")
            await asyncio.sleep(self._poll_interval)

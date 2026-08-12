"""Motor de tareas: lo que hace a JARVIS proactivo y 'pendiente de cosas'.

- `TaskStore`: tareas programadas persistentes (SQLite).
- `Scheduler`: bucle en segundo plano que dispara las tareas cuando toca.
"""

from jarvis_core.tasks.store import Task, TaskStore
from jarvis_core.tasks.scheduler import Scheduler

__all__ = ["Task", "TaskStore", "Scheduler"]

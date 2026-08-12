"""Herramientas de arranque de JARVIS.

`build_default_registry` crea un registro con las herramientas básicas según la config.
Si se pasa un `TaskStore`, registra las herramientas de tareas (proactividad).
Si se pasa un `EmotionState`, registra la herramienta de emoción (color del HUD).
"""

from __future__ import annotations

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.config import Settings
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.memory_tools import RememberTool, RecallTool
from jarvis_core.tools.builtin.task_tools import (
    ScheduleTaskTool,
    ListTasksTool,
    CancelTaskTool,
)
from jarvis_core.tools.builtin.emotion_tool import SetEmotionTool

__all__ = [
    "build_default_registry",
    "SystemInfoTool",
    "ShellTool",
    "RememberTool",
    "RecallTool",
    "ScheduleTaskTool",
    "ListTasksTool",
    "CancelTaskTool",
    "SetEmotionTool",
]


def build_default_registry(
    settings: Settings,
    memory: MemoryStore,
    tasks: TaskStore | None = None,
    emotion: EmotionState | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    registry.register(RememberTool(memory))
    registry.register(RecallTool(memory))
    if settings.enable_shell:
        registry.register(ShellTool(allowlist=settings.shell_allowlist))
    if tasks is not None:
        registry.register(ScheduleTaskTool(tasks))
        registry.register(ListTasksTool(tasks))
        registry.register(CancelTaskTool(tasks))
    if emotion is not None:
        registry.register(SetEmotionTool(emotion))
    return registry

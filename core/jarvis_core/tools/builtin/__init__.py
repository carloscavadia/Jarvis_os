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
from jarvis_core.tools.builtin.emotion_tool import SetEmotionTool
from jarvis_core.tools.builtin.filesystem import (
    WorkspaceGuard,
    register_filesystem_tools,
)
from jarvis_core.tools.builtin.memory_tools import RecallTool, RememberTool
from jarvis_core.tools.builtin.packages import InstallPackageTool
from jarvis_core.tools.builtin.python_runner import RunPythonFileTool
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.task_tools import (
    CancelTaskTool,
    ListTasksTool,
    ScheduleTaskTool,
)

__all__ = [
    "CancelTaskTool",
    "ListTasksTool",
    "RecallTool",
    "RememberTool",
    "ScheduleTaskTool",
    "SetEmotionTool",
    "ShellTool",
    "SystemInfoTool",
    "build_default_registry",
]


def build_default_registry(
    settings: Settings,
    memory: MemoryStore,
    tasks: TaskStore | None = None,
    emotion: EmotionState | None = None,
    *,
    allow_shell: bool | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    registry.register(RememberTool(memory))
    registry.register(RecallTool(memory))
    shell_enabled = settings.enable_shell if allow_shell is None else allow_shell
    if shell_enabled:
        registry.register(ShellTool(allowlist=settings.shell_allowlist))
    if tasks is not None:
        registry.register(ScheduleTaskTool(tasks))
        registry.register(ListTasksTool(tasks))
        registry.register(CancelTaskTool(tasks))
    if emotion is not None:
        registry.register(SetEmotionTool(emotion))
    if settings.agent_control_enabled:
        register_filesystem_tools(
            registry,
            root=settings.workspace_root,
            max_file_bytes=settings.workspace_max_file_bytes,
        )
        if settings.package_install_enabled:
            registry.register(InstallPackageTool(settings.package_install_managers))
        if settings.python_execution_enabled:
            registry.register(
                RunPythonFileTool(
                    WorkspaceGuard(
                        settings.workspace_root,
                        settings.workspace_max_file_bytes,
                    ),
                    timeout=settings.python_execution_timeout_seconds,
                )
            )
    return registry

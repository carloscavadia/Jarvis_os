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
from jarvis_core.tools.builtin.connectors import register_connector_tools
from jarvis_core.tools.builtin.emotion_tool import SetEmotionTool
from jarvis_core.tools.builtin.filesystem import (
    WorkspaceGuard,
    register_filesystem_tools,
)
from jarvis_core.tools.builtin.memory_tools import RecallTool, RememberTool
from jarvis_core.tools.builtin.packages import InstallPackageTool
from jarvis_core.tools.builtin.presentation import ShowInWorkspaceTool
from jarvis_core.tools.builtin.python_runner import RunPythonFileTool
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.task_tools import (
    CancelTaskTool,
    ListTasksTool,
    ScheduleTaskTool,
)
from jarvis_core.tools.builtin.web_tools import register_web_tools

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
    if settings.internet_access_enabled:
        register_web_tools(
            registry,
            timeout=settings.web_request_timeout_seconds,
            max_bytes=settings.web_max_download_bytes,
            brave_api_key=settings.brave_search_api_key,
        )
    if settings.hud_workspace_enabled:
        registry.register(ShowInWorkspaceTool())
    if (
        settings.connectors_enabled
        and settings.n8n_webhook_url
        and settings.n8n_webhook_token
    ):
        register_connector_tools(
            registry,
            webhook_url=settings.n8n_webhook_url,
            token=settings.n8n_webhook_token,
            read_actions=settings.n8n_read_actions,
            write_actions=settings.n8n_write_actions,
            timeout=settings.connector_timeout_seconds,
            max_payload_bytes=settings.connector_max_payload_bytes,
            max_response_bytes=settings.connector_max_response_bytes,
        )
    return registry

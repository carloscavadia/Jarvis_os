"""Herramientas de arranque de JARVIS.

`build_default_registry` crea un registro con las herramientas básicas según la config.
Si se pasa un `TaskStore`, registra las herramientas de tareas (proactividad).
Si se pasa un `EmotionState`, registra la herramienta de emoción (color del HUD).
"""

from __future__ import annotations

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.config import Settings
from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.goals.store import GoalStore
from jarvis_core.memory.store import MemoryStore
from jarvis_core.music.navidrome import build_navidrome_client
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin.connectors import (
    register_connector_tools,
    register_dynamic_connector_tools,
)
from jarvis_core.tools.builtin.emotion_tool import SetEmotionTool
from jarvis_core.tools.builtin.filesystem import (
    WorkspaceGuard,
    register_filesystem_tools,
)
from jarvis_core.tools.builtin.goal_tools import register_goal_tools
from jarvis_core.tools.builtin.memory_tools import RecallTool, RememberTool
from jarvis_core.tools.builtin.music import register_music_tools
from jarvis_core.tools.builtin.packages import InstallPackageTool
from jarvis_core.tools.builtin.presentation import ShowInWorkspaceTool
from jarvis_core.tools.builtin.python_runner import RunPythonFileTool
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.task_tools import (
    CancelTaskTool,
    ListTasksTool,
    PauseTaskTool,
    RescheduleTaskTool,
    ResumeTaskTool,
    ScheduleTaskTool,
    TaskHistoryTool,
    resolve_zone,
)
from jarvis_core.tools.builtin.web_tools import register_web_tools

__all__ = [
    "CancelTaskTool",
    "ListTasksTool",
    "PauseTaskTool",
    "RescheduleTaskTool",
    "ResumeTaskTool",
    "TaskHistoryTool",
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
    connector_store: ConnectorStore | None = None,
    goals: GoalStore | None = None,
    mcp_manager: Any = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    registry.register(RememberTool(memory))
    registry.register(RecallTool(memory))
    shell_enabled = settings.enable_shell if allow_shell is None else allow_shell
    if shell_enabled:
        registry.register(ShellTool(allowlist=settings.shell_allowlist))
    if tasks is not None:
        zone = resolve_zone(settings.timezone)
        registry.register(ScheduleTaskTool(tasks, zone))
        registry.register(ListTasksTool(tasks, zone))
        registry.register(CancelTaskTool(tasks, zone))
        registry.register(PauseTaskTool(tasks, zone))
        registry.register(ResumeTaskTool(tasks, zone))
        registry.register(RescheduleTaskTool(tasks, zone))
        registry.register(TaskHistoryTool(tasks, zone))
    if goals is not None:
        register_goal_tools(registry, goals)
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
    if settings.connectors_enabled and connector_store is not None:
        connector_runtime = ConnectorRuntime(
            connector_store,
            timeout=settings.connector_timeout_seconds,
            max_payload_bytes=settings.connector_max_payload_bytes,
            max_response_bytes=settings.connector_max_response_bytes,
        )
        register_dynamic_connector_tools(
            registry, connector_store, connector_runtime
        )
    if mcp_manager is not None:
        for mcp_tool in mcp_manager.get_registered_tools():
            registry.register(mcp_tool)
    register_music_tools(registry, build_navidrome_client(settings))
    return registry

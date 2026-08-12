"""Tests del sistema de herramientas y la memoria (sin llamar al LLM)."""

import pytest

from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.memory_tools import RememberTool, RecallTool
from jarvis_core.tools.builtin.task_tools import ScheduleTaskTool


async def test_system_info_runs():
    result = await SystemInfoTool().run()
    assert isinstance(result, ToolResult)
    assert "Fecha y hora" in result.content
    assert not result.is_error


async def test_shell_allowlist_blocks_unknown_command():
    tool = ShellTool(allowlist=["echo"])
    result = await tool.run(command="rm -rf /")
    assert result.is_error
    assert "lista blanca" in result.content


async def test_shell_rejects_pipes():
    tool = ShellTool(allowlist=["echo", "cat"])
    result = await tool.run(command="echo hola | cat")
    assert result.is_error


async def test_shell_runs_allowed_command():
    tool = ShellTool(allowlist=["echo"])
    result = await tool.run(command="echo hola-jarvis")
    assert not result.is_error
    assert "hola-jarvis" in result.content


async def test_memory_roundtrip(tmp_path):
    store = MemoryStore(str(tmp_path / "mem.db"))
    remember = RememberTool(store)
    recall = RecallTool(store)

    await remember.run(key="color_favorito", value="azul", tags="preferencias")
    result = await recall.run(query="color")
    assert "azul" in result.content
    store.close()


def test_registry_rejects_duplicates():
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    with pytest.raises(ValueError):
        registry.register(SystemInfoTool())


class SensitiveTool(Tool):
    name = "sensitive"
    description = "Acción sensible de prueba."
    input_schema = {"type": "object", "properties": {}}
    requires_confirmation = True

    async def run(self, **kwargs):
        return ToolResult(content="executed")


async def test_sensitive_tool_is_denied_without_confirmation_policy():
    registry = ToolRegistry()
    registry.register(SensitiveTool())
    orchestrator = Orchestrator(object(), registry, Settings())
    assert not await orchestrator._maybe_confirm("sensitive", {})


async def test_remote_registry_can_exclude_shell(tmp_path):
    from jarvis_core.tools.builtin import build_default_registry

    memory = MemoryStore(str(tmp_path / "memory.db"))
    tasks = TaskStore(str(tmp_path / "tasks.db"))
    try:
        registry = build_default_registry(
            Settings(enable_shell=True),
            memory,
            tasks,
            allow_shell=False,
        )
        assert "run_shell" not in registry.names()
    finally:
        memory.close()
        tasks.close()


async def test_schedule_rejects_negative_or_tight_recurrence(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.db"))
    tool = ScheduleTaskTool(store)
    try:
        negative = await tool.run(
            title="maliciosa",
            prompt="repetir",
            repeat_seconds=-1,
        )
        tight = await tool.run(
            title="costosa",
            prompt="repetir",
            repeat_seconds=5,
        )
        assert negative.is_error
        assert tight.is_error
        assert store.list() == []
    finally:
        store.close()

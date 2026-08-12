"""Tests del sistema de herramientas y la memoria (sin llamar al LLM)."""

import pytest

from jarvis_core.memory.store import MemoryStore
from jarvis_core.tools.base import ToolRegistry, ToolResult
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.memory_tools import RememberTool, RecallTool


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

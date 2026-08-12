"""Tests del sistema de herramientas y la memoria (sin llamar al LLM)."""

from typing import ClassVar

import pytest
from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.base import LLMResponse, ToolCall
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult
from jarvis_core.tools.builtin.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ListDirectoryTool,
    ReadFileTool,
    UpdateFileTool,
    WorkspaceGuard,
)
from jarvis_core.tools.builtin.memory_tools import RecallTool, RememberTool
from jarvis_core.tools.builtin.packages import InstallPackageTool
from jarvis_core.tools.builtin.python_runner import RunPythonFileTool
from jarvis_core.tools.builtin.shell import ShellTool
from jarvis_core.tools.builtin.system_info import SystemInfoTool
from jarvis_core.tools.builtin.task_tools import ScheduleTaskTool
from jarvis_core.tools.builtin.web_tools import (
    _TextExtractor,
    validate_public_https_url,
)


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
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}
    requires_confirmation = True

    async def run(self, **kwargs):
        return ToolResult(content="executed")


class ToolCallingLLM:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, system, history, tools, on_text_delta=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="call-1", name="sensitive", input={})],
                stop_reason="tool_use",
                provider="fake",
            )
        return LLMResponse(text="terminado", provider="fake")


async def test_sensitive_tool_is_denied_without_confirmation_policy():
    registry = ToolRegistry()
    registry.register(SensitiveTool())
    orchestrator = Orchestrator(object(), registry, Settings())
    assert not await orchestrator._maybe_confirm("sensitive", {})


async def test_per_turn_confirmation_can_authorize_sensitive_tool():
    registry = ToolRegistry()
    registry.register(SensitiveTool())
    orchestrator = Orchestrator(ToolCallingLLM(), registry, Settings())
    confirmations = []

    async def approve(name, arguments):
        confirmations.append((name, arguments))
        return True

    reply = await orchestrator.send("hazlo", confirm=approve)
    assert reply.text == "terminado"
    assert reply.tools_used == ["sensitive"]
    assert confirmations == [("sensitive", {})]


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


async def test_workspace_create_list_read_and_update(tmp_path):
    guard = WorkspaceGuard(str(tmp_path / "workspace"), max_file_bytes=1024)
    assert not (await CreateDirectoryTool(guard).run(path="proyecto/docs")).is_error
    assert not (
        await CreateFileTool(guard).run(
            path="proyecto/docs/nota.txt",
            content="hola",
        )
    ).is_error
    listing = await ListDirectoryTool(guard).run(path="proyecto/docs")
    assert "nota.txt" in listing.content
    read = await ReadFileTool(guard).run(path="proyecto/docs/nota.txt")
    assert read.content == "hola"
    updated = await UpdateFileTool(guard).run(
        path="proyecto/docs/nota.txt",
        content=" mundo",
        mode="append",
    )
    assert not updated.is_error
    assert (await ReadFileTool(guard).run(path="proyecto/docs/nota.txt")).content == (
        "hola mundo"
    )


async def test_workspace_blocks_path_escape(tmp_path):
    guard = WorkspaceGuard(str(tmp_path / "workspace"), max_file_bytes=1024)
    registry = ToolRegistry()
    registry.register(ReadFileTool(guard))
    result = await registry.execute("read_file", {"path": "../../etc/passwd"})
    assert result.is_error
    assert "sale del workspace" in result.content


async def test_workspace_blocks_symlink_escape(tmp_path):
    outside = tmp_path / "secreto.txt"
    outside.write_text("no accesible")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "atajo.txt").symlink_to(outside)
    guard = WorkspaceGuard(str(workspace), max_file_bytes=1024)
    registry = ToolRegistry()
    registry.register(ReadFileTool(guard))
    result = await registry.execute("read_file", {"path": "atajo.txt"})
    assert result.is_error
    assert "sale del workspace" in result.content


async def test_create_file_never_overwrites(tmp_path):
    guard = WorkspaceGuard(str(tmp_path / "workspace"), max_file_bytes=1024)
    tool = CreateFileTool(guard)
    assert not (await tool.run(path="nota.txt", content="original")).is_error
    duplicate = await tool.run(path="nota.txt", content="reemplazo")
    assert duplicate.is_error
    assert (tmp_path / "workspace" / "nota.txt").read_text() == "original"


async def test_package_installer_rejects_flags_before_execution():
    tool = InstallPackageTool(["pip"])
    result = await tool.run(manager="pip", package="requests --upgrade")
    assert result.is_error
    assert "inválido" in result.content


async def test_python_runner_executes_only_workspace_python_files(tmp_path):
    guard = WorkspaceGuard(str(tmp_path / "workspace"), max_file_bytes=1024)
    script = guard.root / "saludo.py"
    script.write_text("import sys\nprint('hola', sys.argv[1])\n")
    tool = RunPythonFileTool(guard, timeout=5)

    result = await tool.run(path="saludo.py", arguments=["jefe"])
    assert not result.is_error
    assert "Código de salida: 0" in result.content
    assert "hola jefe" in result.content

    registry = ToolRegistry()
    registry.register(tool)
    blocked = await registry.execute("run_python_file", {"path": "saludo.txt"})
    assert blocked.is_error
    assert "extensión .py" in blocked.content


async def test_python_runner_blocks_symlink_outside_workspace(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("print('no debe ejecutarse')")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape.py").symlink_to(outside)
    registry = ToolRegistry()
    registry.register(
        RunPythonFileTool(WorkspaceGuard(str(workspace), max_file_bytes=1024))
    )
    result = await registry.execute("run_python_file", {"path": "escape.py"})
    assert result.is_error
    assert "sale del workspace" in result.content


def test_web_url_validator_blocks_private_networks(monkeypatch):
    def private_address(*args, **kwargs):
        return [(None, None, None, None, ("192.168.68.100", 443))]

    monkeypatch.setattr("socket.getaddrinfo", private_address)
    with pytest.raises(ValueError, match="privada"):
        validate_public_https_url("https://jarvis.example/status")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_public_https_url("http://example.com")


def test_web_url_validator_accepts_public_https(monkeypatch):
    def public_address(*args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 443))]

    monkeypatch.setattr("socket.getaddrinfo", public_address)
    assert validate_public_https_url("https://example.com/news?q=jarvis") == (
        "https://example.com/news?q=jarvis"
    )


def test_web_text_extractor_omits_scripts_and_keeps_visible_text():
    parser = _TextExtractor()
    parser.feed(
        "<html><title>Noticias</title><script>secreto()</script>"
        "<h1>Terremoto</h1><p>Informe actualizado.</p></html>"
    )
    assert parser.title == "Noticias"
    assert "Terremoto" in parser.text()
    assert "secreto" not in parser.text()


def test_spanish_web_prompt_requires_translation():
    prompt = Settings(language="es", internet_access_enabled=True).system_prompt()
    assert "Responde siempre en español" in prompt
    assert "fuentes pueden estar en cualquier idioma" in prompt
    assert "traducir y redactar" in prompt


async def test_registry_enables_agent_tools_from_settings(tmp_path):
    from jarvis_core.tools.builtin import build_default_registry

    memory = MemoryStore(str(tmp_path / "memory.db"))
    try:
        registry = build_default_registry(
            Settings(
                agent_control_enabled=True,
                workspace_root=str(tmp_path / "workspace"),
                package_install_enabled=True,
                package_install_managers=["pip"],
                python_execution_enabled=True,
                internet_access_enabled=True,
            ),
            memory,
        )
        assert {
            "list_directory",
            "read_file",
            "create_directory",
            "create_file",
            "update_file",
            "install_package",
            "run_python_file",
            "search_web",
            "fetch_web_page",
        }.issubset(registry.names())
    finally:
        memory.close()

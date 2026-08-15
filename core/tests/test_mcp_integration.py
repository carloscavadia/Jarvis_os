"""Pruebas unitarias para la integración de MCP (Model Context Protocol) en JARVIS OS."""

import pytest
from jarvis_core.mcp.manager import MCPManager
from jarvis_core.mcp.store import MCPServerRecord, MCPStore


def test_mcp_store_crud():
    store = MCPStore(":memory:")
    assert len(store.list_all()) == 0

    record = MCPServerRecord(
        name="github",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_TOKEN": "ghp_12345"},
        enabled=True,
    )
    store.save(record)

    all_records = store.list_all()
    assert len(all_records) == 1
    assert all_records[0].name == "github"
    assert all_records[0].command == "npx"
    assert all_records[0].env.get("GITHUB_TOKEN") == "ghp_12345"

    fetched = store.get("github")
    assert fetched is not None
    assert fetched.name == "github"

    store.set_enabled("github", False)
    disabled_rec = store.get("github")
    assert disabled_rec is not None
    assert disabled_rec.enabled is False

    deleted = store.delete("github")
    assert deleted is True
    assert len(store.list_all()) == 0


@pytest.mark.asyncio
async def test_mcp_manager_empty():
    store = MCPStore(":memory:")
    manager = MCPManager(store)

    results = await manager.sync_servers()
    assert len(results) == 0
    assert len(manager.get_registered_tools()) == 0

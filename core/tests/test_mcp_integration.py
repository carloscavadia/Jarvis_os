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


# ── Que un servidor lento no se lleve por delante al gateway ────────────────
#
# `sync_servers` corre en el arranque, antes de aceptar peticiones. Sin tope de
# tiempo, un `npx` que se queda descargando —o un servidor que acepta la
# conexión y no contesta— dejaba al servicio entero sin llegar nunca a estar
# listo. Se descubrió porque colgó la propia suite de tests.


@pytest.mark.asyncio
async def test_a_server_that_never_answers_is_cut_off():
    store = MCPStore(":memory:")
    manager = MCPManager(store, timeout=0.5)
    record = MCPServerRecord(name="lento", transport="stdio", command="sleep", args=["60"])

    with pytest.raises(TimeoutError):
        await manager.connect_server(record)


@pytest.mark.asyncio
async def test_a_slow_server_does_not_block_the_others_at_startup():
    """El arranque sigue adelante y deja fuera al que no responde, en vez de
    quedarse esperando indefinidamente."""
    store = MCPStore(":memory:")
    store.save(MCPServerRecord(name="lento", transport="stdio", command="sleep", args=["60"]))
    store.save(MCPServerRecord(name="pausado", transport="stdio", command="sleep", enabled=False))
    manager = MCPManager(store, timeout=0.5)

    resultados = await manager.sync_servers()

    assert resultados["lento"]["status"] == "error"
    assert "sin respuesta" in resultados["lento"]["error"]
    assert resultados["pausado"]["status"] == "disabled"
    assert manager.get_registered_tools() == []


@pytest.mark.asyncio
async def test_a_tool_call_that_never_returns_gives_up_instead_of_hanging_the_turn():
    store = MCPStore(":memory:")
    store.save(MCPServerRecord(name="lento", transport="stdio", command="sleep", args=["60"]))
    manager = MCPManager(store, timeout=0.5)

    resultado = await manager.call_mcp_tool("lento", "cualquiera", {})

    assert resultado.is_error
    assert "no respondió a tiempo" in resultado.content


def test_the_public_view_of_a_server_hides_its_credentials():
    record = MCPServerRecord(name="github", env={"GITHUB_TOKEN": "ghp_12345"})
    publico = record.to_public_dict()

    assert "ghp_12345" not in str(publico)
    # El nombre de la variable sí se conserva: al editar hace falta saber cuáles
    # están puestas, no cuánto valen.
    assert "GITHUB_TOKEN" in publico["env"]
    # Y la vista interna sigue entregando el valor real, que es la que lanza el
    # proceso.
    assert record.to_dict()["env"]["GITHUB_TOKEN"] == "ghp_12345"

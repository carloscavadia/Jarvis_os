"""Pruebas del registro cifrado y las herramientas modulares."""

import json

from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.tools.base import ToolResult
from jarvis_core.tools.builtin.connectors import (
    ListConnectorModulesTool,
    QueryConnectorModuleTool,
    RunConnectorModuleActionTool,
)


def test_connector_store_encrypts_secrets_and_lists_only_public_data(tmp_path):
    db_path = tmp_path / "connectors.db"
    store = ConnectorStore(str(db_path), "m" * 32)
    try:
        store.upsert(
            "correo",
            "n8n",
            {
                "url": "https://n8n.example.com/webhook/jarvis",
                "services": ["gmail"],
                "read_actions": ["gmail.search"],
                "write_actions": ["gmail.send"],
            },
            {"token": "super-secret-token"},
        )
        assert b"super-secret-token" not in db_path.read_bytes()
        public = store.list_public()[0]
        assert public["services"] == ["gmail"]
        assert "token" not in json.dumps(public)
        assert store.get("correo").config["_secrets"]["token"] == "super-secret-token"
    finally:
        store.close()


async def test_dynamic_tools_enforce_module_action_lists(tmp_path, monkeypatch):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "k" * 32)
    store.upsert(
        "casa",
        "home_assistant",
        {
            "url": "http://homeassistant.local:8123",
            "services": ["homeassistant"],
            "read_actions": ["homeassistant.state"],
            "write_actions": ["homeassistant.service"],
        },
        {"token": "ha-token"},
    )
    runtime = ConnectorRuntime(store)
    calls = []

    def fake_home_assistant(record, action, payload):
        calls.append((record.name, action, payload))
        return ToolResult("ok")

    monkeypatch.setattr(runtime, "_home_assistant", fake_home_assistant)
    listing = await ListConnectorModulesTool(store).run()
    query = QueryConnectorModuleTool(runtime)
    action = RunConnectorModuleActionTool(runtime)
    assert "homeassistant.state" in listing.content
    assert not (
        await query.run(
            connector="casa",
            action="homeassistant.state",
            payload={"entity_id": "light.office"},
        )
    ).is_error
    assert (
        await query.run(connector="casa", action="homeassistant.service", payload={})
    ).is_error
    assert action.requires_confirmation
    assert calls == [("casa", "homeassistant.state", {"entity_id": "light.office"})]
    store.close()

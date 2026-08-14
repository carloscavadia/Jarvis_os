"""Pruebas del registro cifrado y las herramientas modulares."""

import hashlib
import io
import json
import urllib.error
from email.message import Message

import pytest

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
        assert public["url"] == "https://n8n.example.com/webhook/jarvis"
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


async def test_home_assistant_discovers_and_compacts_entities(tmp_path, monkeypatch):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "h" * 32)
    store.upsert(
        "casa",
        "home_assistant",
        {
            "url": "http://homeassistant.local:8123",
            "services": ["homeassistant"],
            # Simula un módulo ya registrado antes de añadir descubrimiento.
            "read_actions": ["homeassistant.state"],
            "write_actions": ["homeassistant.service"],
        },
        {"token": "ha-token"},
    )
    runtime = ConnectorRuntime(store)
    response_body = json.dumps(
        [
            {
                "entity_id": "light.office",
                "state": "on",
                "attributes": {"friendly_name": "Luz oficina"},
            },
            {
                "entity_id": "sensor.temperature",
                "state": "22.4",
                "attributes": {
                    "friendly_name": "Temperatura sala",
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                },
            },
        ]
    ).encode()

    class FakeResponse(io.BytesIO):
        headers = Message()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url.endswith("/api/states")
            assert timeout == runtime.timeout
            return FakeResponse(response_body)

    runtime._opener = FakeOpener()
    result = await runtime.invoke(
        "casa", "homeassistant.entities", {"domain": "sensor"}, write=False
    )
    payload = json.loads(result.content)

    assert not result.is_error
    assert payload["total"] == 1
    assert payload["entities"] == [
        {
            "entity_id": "sensor.temperature",
            "name": "Temperatura sala",
            "domain": "sensor",
            "state": "22.4",
            "device_class": "temperature",
            "unit": "°C",
        }
    ]
    assert "homeassistant.entities" in store.list_public()[0]["read_actions"]
    store.close()


def _service_runtime(tmp_path):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "h" * 32)
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
    return ConnectorRuntime(store)


class _CapturingOpener:
    """Registra la petición y devuelve una respuesta JSON vacía de Home Assistant."""

    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        del timeout
        self.request = request

        class FakeResponse(io.BytesIO):
            headers = Message()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        return FakeResponse(b"[]")


async def test_service_accepts_entity_id_at_the_top_level(tmp_path):
    """La forma natural de Home Assistant, y la que el agente escribe primero.

    Antes se descartaba entity_id en silencio: Home Assistant recibía un cuerpo
    vacío y devolvía un 400 que no explicaba nada.
    """
    runtime = _service_runtime(tmp_path)
    opener = _CapturingOpener()
    runtime._opener = opener

    result = await runtime.invoke(
        "casa",
        "homeassistant.service",
        {"domain": "light", "service": "turn_off", "entity_id": "light.master_room"},
        write=True,
    )

    assert not result.is_error
    assert opener.request.full_url.endswith("/api/services/light/turn_off")
    assert json.loads(opener.request.data) == {"entity_id": "light.master_room"}


async def test_service_still_accepts_the_nested_form(tmp_path):
    runtime = _service_runtime(tmp_path)
    opener = _CapturingOpener()
    runtime._opener = opener

    await runtime.invoke(
        "casa",
        "homeassistant.service",
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.salon", "brightness": 120},
        },
        write=True,
    )

    assert json.loads(opener.request.data) == {
        "entity_id": "light.salon",
        "brightness": 120,
    }


async def test_service_data_must_still_be_an_object_when_given(tmp_path):
    runtime = _service_runtime(tmp_path)
    result = await runtime.invoke(
        "casa",
        "homeassistant.service",
        {"domain": "light", "service": "turn_on", "service_data": "no-es-objeto"},
        write=True,
    )
    assert result.is_error


async def test_http_errors_explain_why(tmp_path):
    """Un 400 sin motivo obliga a adivinar; el cuerpo suele decir qué falta."""
    runtime = _service_runtime(tmp_path)

    class FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                Message(),
                io.BytesIO(b'{"message": "entity_id is required"}'),
            )

    runtime._opener = FailingOpener()
    result = await runtime.invoke(
        "casa",
        "homeassistant.service",
        {"domain": "light", "service": "turn_off"},
        write=True,
    )
    assert result.is_error
    assert "400" in result.content
    assert "entity_id is required" in result.content


# ── Navidrome / Subsonic ─────────────────────────────────────────────────────


def _music_store(tmp_path):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "m" * 32)
    store.upsert(
        "musica",
        "navidrome",
        {"url": "http://192.168.1.10:4533", "username": "carlos", "services": ["musica"]},
        {"password": "sesame"},
        enabled=True,
    )
    return store


def test_subsonic_token_follows_the_protocol():
    """token = md5(contraseña + sal), en hexadecimal minúsculas."""
    from jarvis_core.connectors.runtime import subsonic_auth_params

    params = subsonic_auth_params("carlos", "sesame")
    expected = hashlib.md5(f"sesame{params['s']}".encode()).hexdigest()
    assert params["t"] == expected
    assert params["u"] == "carlos"
    assert params["f"] == "json"
    assert "p" not in params  # la contraseña nunca viaja


def test_subsonic_salt_changes_every_call():
    from jarvis_core.connectors.runtime import subsonic_auth_params

    salts = {subsonic_auth_params("u", "p")["s"] for _ in range(20)}
    assert len(salts) == 20


def test_music_read_actions_are_granted_without_listing_them(tmp_path):
    store = _music_store(tmp_path)
    actions = store.list_public()[0]["read_actions"]
    assert "music.search" in actions and "music.random" in actions


class _SubsonicOpener:
    def __init__(self, body):
        self.body = json.dumps(body).encode()
        self.request = None

    def open(self, request, timeout):
        del timeout
        self.request = request

        class FakeResponse(io.BytesIO):
            headers = Message()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        response = FakeResponse(self.body)
        response.headers["Content-Type"] = "application/json"
        return response


async def test_search_compacts_songs_and_keeps_ids(tmp_path):
    runtime = ConnectorRuntime(_music_store(tmp_path))
    opener = _SubsonicOpener(
        {
            "subsonic-response": {
                "status": "ok",
                "searchResult3": {
                    "song": [
                        {
                            "id": "300",
                            "title": "Bohemian Rhapsody",
                            "artist": "Queen",
                            "album": "A Night at the Opera",
                            "duration": 355,
                            "coverArt": "al-12",
                        },
                        # Pista incompleta: no debe tumbar la búsqueda.
                        {"id": "301", "title": "Sin metadatos"},
                    ]
                },
            }
        }
    )
    runtime._opener = opener

    result = await runtime.invoke(
        "musica", "music.search", {"query": "queen"}, write=False
    )
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 2
    assert payload["songs"][0] == {
        "id": "300",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "duration": 355,
        "cover_art": "al-12",
    }
    assert payload["songs"][1]["title"] == "Sin metadatos"
    assert "search3" in opener.request.full_url


async def test_subsonic_failure_reports_the_real_reason(tmp_path):
    """Subsonic devuelve 200 con el error dentro; sin leerlo parecería un éxito."""
    runtime = ConnectorRuntime(_music_store(tmp_path))
    runtime._opener = _SubsonicOpener(
        {
            "subsonic-response": {
                "status": "failed",
                "error": {"code": 40, "message": "Wrong username or password"},
            }
        }
    )
    result = await runtime.invoke(
        "musica", "music.search", {"query": "queen"}, write=False
    )
    assert result.is_error
    assert "Wrong username or password" in result.content


async def test_music_write_actions_are_never_granted(tmp_path):
    runtime = ConnectorRuntime(_music_store(tmp_path))
    result = await runtime.invoke(
        "musica", "music.search", {"query": "x"}, write=True
    )
    assert result.is_error
    assert "no permitida" in result.content.lower()


def test_media_url_is_authenticated_and_never_leaks_the_password(tmp_path):
    runtime = ConnectorRuntime(_music_store(tmp_path))
    url = runtime.media_url("musica", "stream", {"id": "300"})
    assert url.startswith("http://192.168.1.10:4533/rest/stream?")
    assert "id=300" in url and "u=carlos" in url and "t=" in url
    assert "sesame" not in url


def test_media_url_rejects_a_module_that_is_not_music(tmp_path):
    store = ConnectorStore(str(tmp_path / "c.db"), "m" * 32)
    store.upsert(
        "casa", "home_assistant", {"url": "http://ha.local:8123"}, {"token": "t"}
    )
    runtime = ConnectorRuntime(store)
    with pytest.raises(ValueError):
        runtime.media_url("casa", "stream", {"id": "1"})

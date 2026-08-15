"""Tests del conector de Telegram.

Telegram es distinto de los otros dos módulos en un punto que importa: el token
del bot viaja en la RUTA (`/bot<token>/metodo`), no en una cabecera. Cualquier
mensaje que incluya la URL filtraría la credencial en la traza del HUD y en el
registro del servidor, así que eso es lo primero que se fija aquí.
"""

import json

import pytest
from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.tools.base import ToolResult

TOKEN = "123456789:AAG-secreto-del-bot-que-no-debe-salir"


@pytest.fixture()
def runtime(tmp_path):
    store = ConnectorStore(str(tmp_path / "conectores.db"), "k" * 40)
    store.upsert(
        "telegram",
        "telegram",
        {
            "url": "https://api.telegram.org",
            "read_actions": ["telegram.updates"],
            "write_actions": ["telegram.send"],
            "default_chat_id": "555",
            "chat_ids": ["555", "777"],
        },
        {"token": TOKEN},
    )
    runtime = ConnectorRuntime(store)
    runtime.sent = []

    def fake_request(url, *, method, headers, payload=None):
        runtime.sent.append({"url": url, "method": method, "payload": payload})
        return ToolResult(json.dumps({"ok": True}))

    runtime._request = fake_request
    return runtime


def invoke(runtime, action, payload=None, *, write=False):
    return runtime._invoke("telegram", action, payload or {}, write)


# ── El token ─────────────────────────────────────────────────────────────────


def test_the_token_travels_in_the_path_as_the_protocol_requires(runtime):
    invoke(runtime, "telegram.send", {"chat_id": "555", "text": "hola"}, write=True)
    assert runtime.sent[0]["url"].endswith("/sendMessage")
    assert f"/bot{TOKEN}" in runtime.sent[0]["url"]


def test_a_missing_token_is_reported_without_going_out(runtime):
    runtime.store.upsert(
        "telegram", "telegram",
        {"url": "https://api.telegram.org", "write_actions": ["telegram.send"]},
        {"token": ""},
    )
    result = invoke(runtime, "telegram.send", {"chat_id": "555", "text": "x"}, write=True)
    assert result.is_error
    assert runtime.sent == []


def test_no_error_message_ever_carries_the_token(runtime):
    """Con el token en la URL, cualquier mensaje que la incluya lo filtra."""
    failures = [
        invoke(runtime, "telegram.send", {"chat_id": "555"}, write=True),
        invoke(runtime, "telegram.send", {"chat_id": "999", "text": "x"}, write=True),
        invoke(runtime, "telegram.send", {"chat_id": "555", "text": "x" * 5000}, write=True),
        invoke(runtime, "telegram.inventada", {}, write=True),
        invoke(runtime, "telegram.updates", {"limit": "muchos"}),
    ]
    for result in failures:
        assert result.is_error
        assert TOKEN not in result.content
        assert "api.telegram.org" not in result.content


# ── Envío ────────────────────────────────────────────────────────────────────


def test_sending_uses_the_declared_chat_when_none_is_given(runtime):
    invoke(runtime, "telegram.send", {"text": "hola"}, write=True)
    assert runtime.sent[0]["payload"] == {"chat_id": "555", "text": "hola"}


def test_without_a_destination_nothing_is_sent_at_random(runtime):
    """Sin chat_id y sin destino por defecto, se para en vez de adivinar."""
    runtime.store.upsert(
        "telegram", "telegram",
        {"url": "https://api.telegram.org", "write_actions": ["telegram.send"]},
        {"token": TOKEN},
    )
    result = invoke(runtime, "telegram.send", {"text": "hola"}, write=True)
    assert result.is_error
    assert TOKEN not in result.content
    assert runtime.sent == []


def test_an_unlisted_chat_is_refused(runtime):
    """La aprobación cubre «no envíes esto»; la lista, «no lo envíes ahí»."""
    result = invoke(runtime, "telegram.send", {"chat_id": "999", "text": "x"}, write=True)
    assert result.is_error
    assert runtime.sent == []


def test_without_a_list_any_chat_is_allowed(runtime):
    runtime.store.upsert(
        "telegram", "telegram",
        {"url": "https://api.telegram.org", "write_actions": ["telegram.send"]},
        {"token": TOKEN},
    )
    assert not invoke(
        runtime, "telegram.send", {"chat_id": "42", "text": "x"}, write=True
    ).is_error


def test_a_message_too_long_is_stopped_before_the_round_trip(runtime):
    result = invoke(
        runtime, "telegram.send", {"chat_id": "555", "text": "x" * 4097}, write=True
    )
    assert result.is_error and "4096" in result.content
    assert runtime.sent == []


# ── Separación lectura / escritura ───────────────────────────────────────────


def test_sending_is_a_write_and_cannot_pass_as_a_query(runtime):
    """Enviar exige aprobación, y la aprobación cuelga de la herramienta."""
    result = invoke(runtime, "telegram.send", {"chat_id": "555", "text": "x"})
    assert result.is_error
    assert "escritura" in result.content
    assert runtime.sent == []


def test_identifying_the_bot_is_always_allowed_as_a_read(runtime):
    """Es lo que usa el botón «probar»: no lee mensajes ni cambia nada."""
    assert not invoke(runtime, "telegram.me").is_error
    assert runtime.sent[0]["url"].endswith("/getMe")


def test_reading_updates_is_capped(runtime):
    invoke(runtime, "telegram.updates", {"limit": 5000})
    assert runtime.sent[0]["payload"]["limit"] == 100


async def test_the_test_button_asks_who_the_bot_is(runtime):
    result = await runtime.test("telegram")
    assert not result.is_error
    assert runtime.sent[0]["url"].endswith("/getMe")

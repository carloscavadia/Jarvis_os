"""Empuje de avisos proactivos a Telegram.

Cierra el hueco que hacía inútiles los recordatorios de madrugada: la tarea se
ejecutaba, quedaba registrada, y con el HUD cerrado el aviso no llegaba a nadie.
"""

import pytest
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.tools.base import ToolResult
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.push import build_telegram_push, find_telegram_module

TOKEN = "123456789:AAG-secreto-del-bot-que-no-debe-salir"


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))


class FakeWebSocket:
    def __init__(self, rota=False):
        self.received = []
        self.rota = rota

    async def send_json(self, payload):
        if self.rota:
            raise RuntimeError("cliente caído")
        self.received.append(payload)


@pytest.fixture()
def store(tmp_path):
    return ConnectorStore(str(tmp_path / "conectores.db"), "k" * 40)


def register(store, **overrides):
    config = {
        "url": "https://api.telegram.org",
        "write_actions": ["telegram.send"],
        "default_chat_id": "555",
    }
    config.update(overrides)
    store.upsert("telegram", "telegram", config, {"token": TOKEN},
                 enabled=overrides.pop("enabled", True))


# ── A quién se le empuja ─────────────────────────────────────────────────────


def test_a_module_without_a_fixed_chat_is_not_usable(store):
    """Sin destino fijo habría que elegirlo en cada envío, y elegir
    destinatario es justo lo que no debe hacerse sin decisión del usuario."""
    register(store, default_chat_id="")
    assert find_telegram_module(store) is None


def test_a_disabled_module_is_not_used(store):
    store.upsert("telegram", "telegram",
                 {"url": "https://api.telegram.org", "default_chat_id": "555"},
                 {"token": TOKEN}, enabled=False)
    assert find_telegram_module(store) is None


def test_a_ready_module_is_found(store):
    register(store)
    assert find_telegram_module(store) == "telegram"


def test_without_a_store_there_is_no_push(store):
    assert build_telegram_push(None) is None


# ── El envío ─────────────────────────────────────────────────────────────────


async def test_the_notice_goes_out_with_the_configured_chat(store, monkeypatch):
    register(store)
    push = build_telegram_push(store)
    enviados = []

    async def fake_invoke(self, connector, action, payload, *, write):
        enviados.append((connector, action, payload, write))
        return ToolResult("{}")

    monkeypatch.setattr("jarvis_core.connectors.runtime.ConnectorRuntime.invoke",
                        fake_invoke)
    await push("Recuerda la reunión", "task")

    connector, action, payload, write = enviados[0]
    assert (connector, action, write) == ("telegram", "telegram.send", True)
    # El destino no viaja en la petición: lo pone el módulo, así que ni el
    # modelo ni un evento externo pueden redirigir el aviso.
    assert "chat_id" not in payload
    assert "Recuerda la reunión" in payload["text"]


async def test_a_module_registered_later_starts_receiving_without_a_restart(
    store, monkeypatch
):
    """El panel permite dar de alta el módulo con el gateway en marcha."""
    push = build_telegram_push(store)
    enviados = []

    async def fake_invoke(self, connector, action, payload, *, write):
        enviados.append(connector)
        return ToolResult("{}")

    monkeypatch.setattr("jarvis_core.connectors.runtime.ConnectorRuntime.invoke",
                        fake_invoke)

    await push("antes de registrarlo", "task")
    assert enviados == []

    register(store)
    await push("después", "task")
    assert enviados == ["telegram"]


# ── Aislamiento ──────────────────────────────────────────────────────────────


async def test_a_telegram_failure_never_blocks_the_delivery_already_made():
    """Lo que ya se entregó al HUD no puede depender de que Telegram responda."""
    mqtt = FakeMqtt()
    ws = FakeWebSocket()
    notifier = Notifier(mqtt)
    notifier.add_ws(ws)

    async def push_roto(text, source):
        raise RuntimeError("Telegram no responde")

    notifier.push_sink = push_roto
    await notifier.broadcast("Recuerda la reunión", source="task")

    assert ws.received[0]["text"] == "Recuerda la reunión"
    assert mqtt.published, "el aviso debe salir igualmente por MQTT"


async def test_a_broken_websocket_does_not_stop_the_push():
    """El orden inverso también: un HUD caído no puede impedir el aviso al móvil."""
    notifier = Notifier(FakeMqtt())
    notifier.add_ws(FakeWebSocket(rota=True))
    empujados = []

    async def push(text, source):
        empujados.append(text)

    notifier.push_sink = push
    await notifier.broadcast("Recuerda la reunión", source="task")

    assert empujados == ["Recuerda la reunión"]
    # Y el cliente muerto se limpia.
    assert not notifier._websockets


async def test_without_a_sink_the_broadcast_works_as_before():
    notifier = Notifier(FakeMqtt())
    ws = FakeWebSocket()
    notifier.add_ws(ws)
    await notifier.broadcast("hola", source="jarvis")
    assert ws.received[0]["text"] == "hola"

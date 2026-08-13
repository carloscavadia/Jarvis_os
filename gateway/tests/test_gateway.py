"""Pruebas de integración del servicio principal sin consumir un LLM real."""

import json

from fastapi.testclient import TestClient
from jarvis_core.agent.orchestrator import AgentReply
from jarvis_core.connectors.events import ProactiveEventStore
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.goals.store import GoalStore
from jarvis_core.tools.base import ToolResult
from jarvis_gateway import app as gateway_module
from jarvis_gateway.voice import prepare_speech_text


class FakeOrchestrator:
    async def send(
        self, message: str, on_text_delta=None, confirm=None, on_tool_event=None
    ) -> AgentReply:
        if on_text_delta is not None:
            await on_text_delta("eco:")
            await on_text_delta(message)
        return AgentReply(
            text=f"eco:{message}",
            tools_used=["system_info"],
            emotion="focused",
        )


class ApprovalOrchestrator:
    async def send(
        self, message: str, on_text_delta=None, confirm=None, on_tool_event=None
    ) -> AgentReply:
        assert confirm is not None
        assert on_tool_event is not None
        arguments = {"manager": "pip", "package": "requests"}
        await on_tool_event("proposed", "install_package", arguments, None)
        approved = await confirm(
            "install_package",
            arguments,
        )
        await on_tool_event("running", "install_package", arguments, None)
        await on_tool_event(
            "completed",
            "install_package",
            arguments,
            ToolResult("Successfully installed requests"),
        )
        return AgentReply(
            text="Instalación autorizada." if approved else "Instalación denegada.",
            emotion="focused",
        )


class FakeVoiceRuntime:
    def status(self) -> dict[str, str | bool]:
        return {"enabled": True, "stt": "fake", "tts": "fake.onnx"}

    async def transcribe(self, audio: bytes) -> str:
        assert audio == b"fake-audio"
        return "hola por voz"

    async def synthesize(self, text: str) -> bytes:
        assert text == "respuesta hablada"
        return b"RIFF-fake-wave"


class FakeNotifier:
    def __init__(self) -> None:
        self.events = []

    async def broadcast(self, text, source="jarvis", **extra):
        self.events.append((text, source, extra))

    def add_ws(self, ws):
        pass

    def remove_ws(self, ws):
        pass


async def _fake_get(session_id: str) -> FakeOrchestrator:
    return FakeOrchestrator()


async def _approval_get(session_id: str) -> ApprovalOrchestrator:
    return ApprovalOrchestrator()


def test_prepare_speech_text_removes_markdown_symbols_and_urls():
    raw = "## Estado\n- **CPU:** `normal`\n- [Documentación](https://example.com)\n```sh\necho hola\n```"
    assert prepare_speech_text(raw) == "Estado CPU: normal Documentación"


def test_tool_arguments_hide_content_and_unknown_sensitive_fields():
    assert gateway_module._public_approval_arguments(
        {"path": "script.py", "content": "print('hola')", "secret": "oculto"}
    ) == {"path": "script.py", "content_bytes": 13}
    assert gateway_module._public_approval_arguments(
        {"url": "https://example.com/noticia?token=secreto#parte"}
    ) == {"url": "https://example.com/noticia"}
    assert gateway_module._public_approval_arguments(
        {
            "action": "gmail.send",
            "payload": {"to": "jefe@example.com", "body": "privado"},
        }
    ) == {
        "action": "gmail.send",
        "payload_bytes": 45,
        "payload_preview": {"to": "jefe@example.com", "body": "privado"},
    }
    assert gateway_module._redact_connector_payload(
        {"to": "jefe@example.com", "api_key": "secreta"}
    ) == {"to": "jefe@example.com", "api_key": "[oculto]"}


def test_workspace_presentation_exposes_only_the_visual_payload():
    assert gateway_module._workspace_presentation("read_file", {"content": "x"}) is None
    assert gateway_module._workspace_presentation(
        "show_in_workspace",
        {
            "title": " Código ",
            "content": "print('hola')",
            "format": "code",
            "language": "python",
            "keep_open": True,
            "secret": "no reenviar",
        },
    ) == {
        "title": "Código",
        "content": "print('hola')",
        "format": "code",
        "language": "python",
        "keep_open": True,
    }


def test_gateway_health_auth_chat_and_websocket(monkeypatch):
    monkeypatch.setattr(gateway_module.sessions, "get", _fake_get)
    monkeypatch.setattr(gateway_module, "voice_runtime", FakeVoiceRuntime())

    with TestClient(gateway_module.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        readiness = client.get("/ready")
        assert readiness.status_code == 200
        assert readiness.json()["status"] == "ready"

        unauthorized = client.post("/chat", json={"message": "hola"})
        assert unauthorized.status_code == 401

        response = client.post(
            "/chat",
            headers={"X-Jarvis-Key": "ci-test-key"},
            json={"message": "hola", "session_id": "test-hub"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "reply": "eco:hola",
            "tools_used": ["system_info"],
            "session_id": "test-hub",
        }

        voice_status = client.get(
            "/voice/status", headers={"X-Jarvis-Key": "ci-test-key"}
        )
        assert voice_status.status_code == 200
        assert voice_status.json()["enabled"] is True

        transcription = client.post(
            "/voice/transcribe",
            headers={
                "X-Jarvis-Key": "ci-test-key",
                "Content-Type": "audio/webm",
            },
            content=b"fake-audio",
        )
        assert transcription.status_code == 200
        assert transcription.json() == {"text": "hola por voz"}

        speech = client.post(
            "/voice/synthesize",
            headers={"X-Jarvis-Key": "ci-test-key"},
            json={"text": "respuesta hablada"},
        )
        assert speech.status_code == 200
        assert speech.headers["content-type"] == "audio/wav"
        assert speech.content == b"RIFF-fake-wave"

        unauthorized_voice = client.get("/voice/status")
        assert unauthorized_voice.status_code == 401
        preflight = client.options(
            "/voice/transcribe",
            headers={
                "Origin": "http://127.0.0.1:4173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Jarvis-Key,Content-Type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == (
            "http://127.0.0.1:4173"
        )

        invalid_session = client.post(
            "/chat",
            headers={"X-Jarvis-Key": "ci-test-key"},
            json={"message": "hola", "session_id": "../otro"},
        )
        assert invalid_session.status_code == 422

        with client.websocket_connect("/ws/test-hub?token=ci-test-key") as websocket:
            assert websocket.receive_json() == {"type": "state", "state": "idle"}
            websocket.send_text("estado")
            assert websocket.receive_json() == {"type": "state", "state": "listening"}
            assert websocket.receive_json() == {"type": "state", "state": "thinking"}
            assert websocket.receive_json() == {"type": "state", "state": "speaking"}
            assert websocket.receive_json() == {"type": "reply_start"}
            assert websocket.receive_json() == {"type": "reply_delta", "delta": "eco:"}
            assert websocket.receive_json() == {
                "type": "reply_delta",
                "delta": "estado",
            }
            assert websocket.receive_json() == {"type": "emotion", "emotion": "focused"}
            assert websocket.receive_json() == {
                "type": "event",
                "event": "execution",
                "label": "system_info",
            }
            assert websocket.receive_json() == {
                "type": "reply",
                "reply": "eco:estado",
                "tools_used": ["system_info"],
            }
            assert websocket.receive_json() == {"type": "state", "state": "idle"}


def test_goal_supervisor_recovers_and_controls_blocked_goal(monkeypatch, tmp_path):
    store = GoalStore(str(tmp_path / "goals.db"))
    monkeypatch.setattr(gateway_module.sessions, "goals", store)
    goal_id = store.create(
        "Desplegar servicio",
        "Actualizar y verificar JARVIS.",
        [
            {"title": "Actualizar", "verification": "Commit desplegado"},
            {"title": "Verificar", "verification": "Health OK"},
        ],
    )
    headers = {"X-Jarvis-Key": "ci-test-key"}
    try:
        with TestClient(gateway_module.app) as client:
            assert client.get("/goals/current").status_code == 401

            current = client.get("/goals/current", headers=headers)
            assert current.status_code == 200
            assert current.json()["goal"]["goal_id"] == goal_id

            blocked = client.post(
                f"/goals/{goal_id}/control",
                headers=headers,
                json={"action": "block"},
            )
            assert blocked.status_code == 200
            assert blocked.json()["status"] == "blocked"
            assert client.get("/goals/current", headers=headers).json()["active"] is False

            resumed = client.post(
                f"/goals/{goal_id}/control",
                headers=headers,
                json={"action": "resume"},
            )
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "active"
            assert [event["type"] for event in resumed.json()["events"]] == [
                "created",
                "blocked",
                "resumed",
            ]
    finally:
        store.close()


def test_connector_ingress_uses_separate_auth_and_private_sessions(
    monkeypatch, tmp_path
):
    fake_notifier = FakeNotifier()
    event_store = ProactiveEventStore(str(tmp_path / "events.db"))
    connector_token = "c" * 32
    monkeypatch.setattr(gateway_module.settings, "connectors_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "n8n_webhook_token", connector_token)
    identity_hash = gateway_module.hashlib.sha256(b"whatsapp:+15551234567").hexdigest()
    monkeypatch.setattr(gateway_module.settings, "connector_chat_enabled", True)
    monkeypatch.setattr(
        gateway_module.settings, "connector_allowed_user_hashes", [identity_hash]
    )
    monkeypatch.setattr(gateway_module.sessions, "get", _fake_get)
    monkeypatch.setattr(gateway_module.sessions, "proactive_events", event_store)
    monkeypatch.setattr(gateway_module, "notifier", fake_notifier)

    with TestClient(gateway_module.app) as client:
        unauthorized = client.post(
            "/connectors/chat",
            json={
                "connector": "whatsapp",
                "external_user": "+15551234567",
                "message": "hola",
            },
        )
        assert unauthorized.status_code == 401

        headers = {"X-Jarvis-Connector-Token": connector_token}
        response = client.post(
            "/connectors/chat",
            headers=headers,
            json={
                "connector": "whatsapp",
                "external_user": "+15551234567",
                "message": "hola",
            },
        )
        assert response.status_code == 200
        assert (
            response.json()["reply"] == "eco:[Mensaje recibido mediante whatsapp]\nhola"
        )
        assert response.json()["session_id"].startswith("connector-")
        assert "+15551234567" not in response.json()["session_id"]

        event = client.post(
            "/connectors/events",
            headers=headers,
            json={
                "connector": "gmail",
                "event": "new_message",
                "title": "Correo nuevo",
                "text": "Llegó un correo importante.",
            },
        )
        assert event.json() == {"status": "accepted", "event_id": 1}
        assert fake_notifier.events == [
            (
                "Llegó un correo importante.",
                "connector:gmail",
                {
                    "connector": "gmail",
                    "event": "new_message",
                    "title": "Correo nuevo",
                    "severity": "info",
                    "policy": "notify",
                    "status": "accepted",
                    "event_id": 1,
                    "action": "",
                    "payload": {},
                    "goal": None,
                },
            )
        ]
    event_store.close()


def test_proactive_write_action_requires_and_respects_denial(monkeypatch, tmp_path):
    connector_store = ConnectorStore(
        str(tmp_path / "connectors.db"), "master-key-" * 4
    )
    connector_store.upsert(
        "home",
        "home_assistant",
        {
            "url": "http://home.local:8123",
            "services": ["lights"],
            "read_actions": ["homeassistant.state"],
            "write_actions": ["homeassistant.service"],
        },
        {"token": "secret-token"},
    )
    event_store = ProactiveEventStore(str(tmp_path / "events.db"))
    fake_notifier = FakeNotifier()
    monkeypatch.setattr(gateway_module.settings, "connectors_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "n8n_webhook_token", "c" * 32)
    monkeypatch.setattr(gateway_module.sessions, "connector_store", connector_store)
    monkeypatch.setattr(gateway_module.sessions, "proactive_events", event_store)
    monkeypatch.setattr(gateway_module, "notifier", fake_notifier)

    with TestClient(gateway_module.app) as client:
        incoming = client.post(
            "/connectors/events",
            headers={"X-Jarvis-Connector-Token": "c" * 32},
            json={
                "connector": "home",
                "event": "presence.detected",
                "title": "Llegada",
                "text": "Se detectó presencia.",
                "action": "homeassistant.service",
                "payload": {
                    "domain": "light",
                    "service": "turn_on",
                    "api_key": "no-exponer",
                },
            },
        )
        assert incoming.status_code == 200
        assert incoming.json() == {"status": "pending_approval", "event_id": 1}

        pending = client.get(
            "/proactive/events", headers={"X-Jarvis-Key": "ci-test-key"}
        ).json()[0]
        assert pending["status"] == "pending_approval"
        assert pending["payload"]["api_key"] == "[oculto]"

        denied = client.post(
            "/proactive/events/1/decision",
            headers={"X-Jarvis-Key": "ci-test-key"},
            json={"approved": False},
        )
        assert denied.status_code == 200
        assert denied.json()["status"] == "denied"
        assert event_store.get(1)["status"] == "denied"

    event_store.close()
    connector_store.close()


def test_gateway_websocket_approval(monkeypatch):
    monkeypatch.setattr(gateway_module.sessions, "get", _approval_get)
    with (
        TestClient(gateway_module.app) as client,
        client.websocket_connect("/ws/approval-hub?token=ci-test-key") as websocket,
    ):
        assert websocket.receive_json() == {"type": "state", "state": "idle"}
        websocket.send_text("instala requests")
        assert websocket.receive_json() == {"type": "state", "state": "listening"}
        assert websocket.receive_json() == {"type": "state", "state": "thinking"}
        proposed = websocket.receive_json()
        assert proposed == {
            "type": "tool_event",
            "phase": "proposed",
            "tool": "install_package",
            "arguments": {"manager": "pip", "package": "requests"},
        }
        approval = websocket.receive_json()
        assert approval["type"] == "approval_required"
        assert approval["tool"] == "install_package"
        assert approval["arguments"] == {
            "manager": "pip",
            "package": "requests",
        }
        websocket.send_json(
            {
                "type": "approval",
                "approval_id": approval["approval_id"],
                "approved": True,
            }
        )
        resolved = websocket.receive_json()
        assert resolved == {
            "type": "approval_resolved",
            "approval_id": approval["approval_id"],
            "approved": True,
            "reason": "user",
        }
        assert websocket.receive_json() == {
            "type": "tool_event",
            "phase": "running",
            "tool": "install_package",
            "arguments": {"manager": "pip", "package": "requests"},
        }
        assert websocket.receive_json() == {
            "type": "tool_event",
            "phase": "completed",
            "tool": "install_package",
            "arguments": {"manager": "pip", "package": "requests"},
            "output": "Successfully installed requests",
            "is_error": False,
        }
        assert websocket.receive_json() == {"type": "state", "state": "speaking"}
        assert websocket.receive_json() == {"type": "reply_start"}
        assert websocket.receive_json() == {
            "type": "reply_delta",
            "delta": "Instalación autorizada.",
        }
        assert websocket.receive_json() == {"type": "emotion", "emotion": "focused"}
        assert websocket.receive_json() == {
            "type": "reply",
            "reply": "Instalación autorizada.",
            "tools_used": [],
        }
        assert websocket.receive_json() == {"type": "state", "state": "idle"}


def test_ready_rejects_incomplete_connector_configuration(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "connectors_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "n8n_webhook_url", "")
    monkeypatch.setattr(gateway_module.settings, "n8n_webhook_token", "short")

    with TestClient(gateway_module.app) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        missing = response.json()["detail"]["missing_or_invalid"]
        assert "n8n_webhook_url" in missing
        assert "n8n_webhook_token" in missing


def test_connector_module_api_never_returns_secrets(monkeypatch, tmp_path):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "master-key-" * 4)
    monkeypatch.setattr(gateway_module.sessions, "connector_store", store)
    try:
        with TestClient(gateway_module.app) as client:
            unauthorized = client.get("/connector-modules")
            assert unauthorized.status_code == 401

            headers = {"X-Jarvis-Key": "ci-test-key"}
            response = client.put(
                "/connector-modules/correo",
                headers=headers,
                json={
                    "name": "correo",
                    "type": "n8n",
                    "url": "https://n8n.example.com/webhook/jarvis",
                    "token": "token-secreto-de-prueba",
                    "services": ["gmail"],
                    "read_actions": ["gmail.search", "connector.test"],
                    "write_actions": ["gmail.send"],
                },
            )
            assert response.status_code == 200
            listing = client.get("/connector-modules", headers=headers)
            assert listing.status_code == 200
            serialized = json.dumps(listing.json())
            assert "gmail.search" in serialized
            assert "token-secreto-de-prueba" not in serialized
            updated = client.put(
                "/connector-modules/correo",
                headers=headers,
                json={
                    "name": "correo",
                    "type": "n8n",
                    "url": "https://n8n.example.com/webhook/jarvis-v2",
                    "token": "",
                    "services": ["gmail", "outlook"],
                    "read_actions": ["gmail.search"],
                    "write_actions": ["gmail.send"],
                    "enabled": False,
                },
            )
            assert updated.status_code == 200
            assert store.get("correo").config["_secrets"]["token"] == (
                "token-secreto-de-prueba"
            )
            assert client.get("/connector-modules", headers=headers).json()[0][
                "enabled"
            ] is False
            assert client.delete("/connector-modules/correo", headers=headers).json()[
                "deleted"
            ]
    finally:
        store.close()

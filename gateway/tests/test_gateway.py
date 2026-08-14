"""Pruebas de integración del servicio principal sin consumir un LLM real."""

import asyncio
import json
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from jarvis_core.agent.orchestrator import AgentReply
from jarvis_core.connectors.events import ProactiveEventStore
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.goals.store import GoalStore
from jarvis_core.tools.base import ToolResult
from jarvis_core.voice import WakeWordDetector
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


class FakeWakeDetector(WakeWordDetector):
    """Sustituye a openWakeWord: activa cuando el audio no es silencio.

    Hereda del detector real para que las constantes del protocolo (frecuencia,
    tamaño de frame y tope de fragmento) sean las de producción y no una copia
    que pueda quedarse desfasada.
    """

    instances: ClassVar[list["FakeWakeDetector"]] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunks: list[bytes] = []
        self.resets = 0
        FakeWakeDetector.instances.append(self)

    def warm_up(self) -> None:
        """No carga nada: evita descargar openWakeWord durante las pruebas."""

    def process(self, pcm: bytes) -> float | None:
        self.chunks.append(pcm)
        return 0.93 if any(pcm) else None

    def reset(self) -> None:
        self.resets += 1


def test_wakeword_stream_requires_auth_and_reports_detections(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "voice_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "wakeword_enabled", True)
    monkeypatch.setattr(gateway_module, "WakeWordDetector", FakeWakeDetector)
    FakeWakeDetector.instances.clear()

    with TestClient(gateway_module.app) as client:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws/wake/hud?token=incorrecta"),
        ):
            pass

        with client.websocket_connect("/ws/wake/hud?token=ci-test-key") as socket:
            assert socket.receive_json()["type"] == "wake_ready"

            # El silencio no activa y no genera tráfico de vuelta.
            socket.send_bytes(b"\x00\x00" * 1280)
            socket.send_bytes(b"\x11\x22" * 1280)
            message = socket.receive_json()
            assert message["type"] == "wake"
            assert message["score"] == 0.93
            assert message["device"] == "hud"

            # «reset» descarta el audio previo a una respuesta de JARVIS.
            socket.send_text("reset")
            socket.send_bytes(b"\x11\x22" * 1280)
            assert socket.receive_json()["type"] == "wake"

    detector = FakeWakeDetector.instances[-1]
    assert detector.resets == 1
    assert len(detector.chunks) == 3


def test_wakeword_stream_closes_when_disabled(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "voice_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "wakeword_enabled", False)

    with (
        TestClient(gateway_module.app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/wake/hud?token=ci-test-key"),
    ):
        pass


def test_wakeword_stream_rejects_oversized_chunks(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "voice_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "wakeword_enabled", True)
    monkeypatch.setattr(gateway_module, "WakeWordDetector", FakeWakeDetector)

    with (
        TestClient(gateway_module.app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/wake/hud?token=ci-test-key") as socket,
    ):
        socket.receive_json()
        socket.send_bytes(b"\x01" * (WakeWordDetector.MAX_BUFFER_BYTES + 1))
        socket.receive_json()


def test_voice_status_exposes_wakeword(monkeypatch):
    monkeypatch.setattr(gateway_module, "voice_runtime", FakeVoiceRuntime())
    monkeypatch.setattr(gateway_module.settings, "voice_enabled", True)
    monkeypatch.setattr(gateway_module.settings, "wakeword_enabled", True)

    with TestClient(gateway_module.app) as client:
        status = client.get(
            "/voice/status", headers={"X-Jarvis-Key": "ci-test-key"}
        ).json()
    assert status["wakeword"] == {
        "enabled": True,
        "model": "hey_jarvis",
        "sample_rate": 16000,
        "frame_samples": 1280,
        "engine": "openwakeword",
    }


class FakeConversation:
    """Sustituye la sesión Realtime: registra el audio y dispara la aprobación."""

    instances: ClassVar[list["FakeConversation"]] = []

    def __init__(
        self, settings, registry, *, on_audio, on_event, confirm=None, on_usage=None
    ):
        self.settings = settings
        self.registry = registry
        self.on_audio = on_audio
        self.on_event = on_event
        self.confirm = confirm
        self.on_usage = on_usage
        self.audio: list[bytes] = []
        self.cancels = 0
        self.closed = False
        self.approval_result: bool | None = None
        self.usage = {"input_audio_tokens": 7, "output_audio_tokens": 11}
        self._released = asyncio.Event()
        FakeConversation.instances.append(self)

    async def connect(self):
        pass

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def cancel_response(self):
        self.cancels += 1

    async def pump(self):
        # Devuelve audio y pide aprobación, como haría una respuesta real.
        await self.on_audio(b"pcm-de-jarvis")
        await self.on_event({"type": "state", "state": "speaking"})
        if self.confirm is not None:
            self.approval_result = await self.confirm(
                "install_package", {"manager": "pip", "package": "requests"}
            )
        self._released.set()
        await asyncio.Event().wait()  # se queda viva hasta que la cancelen

    async def close(self):
        self.closed = True


def _enable_realtime(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "realtime_conversation_enabled", True)
    monkeypatch.setattr(gateway_module, "RealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        gateway_module.realtime_voice, "budget_available", lambda: True
    )
    monkeypatch.setattr(gateway_module.realtime_voice, "record_session", lambda: None)
    FakeConversation.instances.clear()


def test_realtime_voice_requires_auth_and_being_enabled(monkeypatch):
    monkeypatch.setattr(
        gateway_module.settings, "realtime_conversation_enabled", False
    )
    with (
        TestClient(gateway_module.app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/voice/hud?token=ci-test-key"),
    ):
        pass

    _enable_realtime(monkeypatch)
    with (
        TestClient(gateway_module.app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/voice/hud?token=incorrecta"),
    ):
        pass


def test_realtime_voice_streams_audio_and_resolves_approvals(monkeypatch):
    _enable_realtime(monkeypatch)
    recorded: list[dict] = []
    monkeypatch.setattr(
        gateway_module.realtime_voice, "record_usage", recorded.append
    )

    with (
        TestClient(gateway_module.app) as client,
        client.websocket_connect("/ws/voice/hud?token=ci-test-key") as socket,
    ):
        assert socket.receive_json() == {
            "type": "voice_ready",
            "sample_rate": 24000,
            "model": gateway_module.settings.openai_realtime_model,
        }
        # El audio de JARVIS llega como binario, no como JSON.
        assert socket.receive_bytes() == b"pcm-de-jarvis"
        assert socket.receive_json() == {"type": "state", "state": "speaking"}

        approval = socket.receive_json()
        assert approval["type"] == "approval_required"
        assert approval["tool"] == "install_package"
        assert approval["arguments"] == {"manager": "pip", "package": "requests"}

        # El micrófono sigue enviando mientras la aprobación está pendiente.
        socket.send_bytes(b"audio-del-usuario")
        socket.send_text(
            json.dumps(
                {
                    "type": "approval",
                    "approval_id": approval["approval_id"],
                    "approved": True,
                }
            )
        )
        resolved = socket.receive_json()
        assert resolved["type"] == "approval_resolved"
        assert resolved["approved"] is True

    conversation = FakeConversation.instances[-1]
    assert conversation.approval_result is True
    assert b"audio-del-usuario" in conversation.audio
    assert conversation.closed
    # El consumo se contabiliza al cerrar, aunque el cliente se desconecte.
    assert recorded == [conversation.usage]


def test_realtime_voice_relays_cancel_and_ignores_junk(monkeypatch):
    _enable_realtime(monkeypatch)
    monkeypatch.setattr(gateway_module.realtime_voice, "record_usage", lambda usage: None)

    with (
        TestClient(gateway_module.app) as client,
        client.websocket_connect("/ws/voice/hud?token=ci-test-key") as socket,
    ):
        socket.receive_json()
        socket.receive_bytes()
        socket.receive_json()
        approval = socket.receive_json()
        socket.send_text("esto no es json")
        socket.send_text(json.dumps({"type": "desconocido"}))
        socket.send_text(json.dumps({"type": "cancel"}))
        socket.send_text(
            json.dumps(
                {
                    "type": "approval",
                    "approval_id": approval["approval_id"],
                    "approved": False,
                }
            )
        )
        assert socket.receive_json()["approved"] is False

    conversation = FakeConversation.instances[-1]
    assert conversation.cancels == 1


class BudgetConversation(FakeConversation):
    """Reporta un consumo enorme en la primera respuesta de la conversación."""

    async def pump(self):
        await self.on_event({"type": "state", "state": "speaking"})
        if self.on_usage is not None:
            await self.on_usage(
                {"output_tokens": 5_000_000, "output_audio_tokens": 5_000_000}
            )
        await asyncio.Event().wait()


def test_realtime_voice_stops_when_the_daily_budget_is_spent(monkeypatch):
    _enable_realtime(monkeypatch)
    monkeypatch.setattr(gateway_module, "RealtimeConversation", BudgetConversation)
    monkeypatch.setattr(gateway_module.settings, "realtime_daily_budget_usd", 0.05)
    monkeypatch.setattr(gateway_module.realtime_voice, "spend_today", lambda: 0.0)
    monkeypatch.setattr(gateway_module.realtime_voice, "record_usage", lambda usage: None)

    with (
        TestClient(gateway_module.app) as client,
        client.websocket_connect("/ws/voice/hud?token=ci-test-key") as socket,
    ):
        socket.receive_json()  # voice_ready
        assert socket.receive_json() == {"type": "state", "state": "speaking"}
        # El bucle corta en cuanto llega el siguiente fragmento de audio.
        socket.send_bytes(b"audio")
        with pytest.raises(WebSocketDisconnect):
            while True:
                message = socket.receive_json()
                # El gasto nunca se comunica al cliente.
                assert "usd" not in json.dumps(message).lower()

    assert FakeConversation.instances[-1].closed


def test_voice_status_never_reports_spending(monkeypatch):
    monkeypatch.setattr(gateway_module, "voice_runtime", FakeVoiceRuntime())
    with TestClient(gateway_module.app) as client:
        status = client.get(
            "/voice/status", headers={"X-Jarvis-Key": "ci-test-key"}
        ).json()
    assert "usd" not in json.dumps(status).lower()
    assert "spend" not in json.dumps(status).lower()


def test_music_command_only_reacts_to_music_tools():
    assert gateway_module._music_command("read_file", ToolResult("{}")) is None
    assert gateway_module._music_command("play_music", None) is None
    # Un error no debe abrir el reproductor con una cola vacía.
    assert gateway_module._music_command(
        "play_music", ToolResult("fallo", is_error=True)
    ) is None
    assert gateway_module._music_command("play_music", ToolResult("no-es-json")) is None
    assert gateway_module._music_command("play_music", ToolResult('{"queue": []}')) is None


def test_music_command_builds_the_player_order():
    result = ToolResult(
        json.dumps(
            {
                "queue": [{"id": "7", "title": "Uno"}, {"sin_id": True}],
                "source": "queen",
            }
        )
    )
    command = gateway_module._music_command("play_music", result)
    assert command == {
        "command": "play",
        "source": "queen",
        "queue": [{"id": "7", "title": "Uno"}],
    }


def test_music_control_passes_the_command_through():
    command = gateway_module._music_command(
        "control_music", ToolResult('{"command": "pause"}')
    )
    assert command == {"command": "pause"}


def test_music_stream_requires_the_gateway_key(monkeypatch):
    with TestClient(gateway_module.app) as client:
        assert client.get("/music/stream/7").status_code == 401
        assert client.get("/music/stream/7?token=mala").status_code == 401
        # Identificadores fuera de forma no llegan al servidor de música.
        assert client.get(
            "/music/stream/../secreto?token=ci-test-key"
        ).status_code in {404, 422}
        # Sin servidor de música configurado no hay nada que servir.
        assert client.get("/music/stream/7?token=ci-test-key").status_code == 404


def test_health_reports_features_so_version_mismatches_are_visible():
    """Un HUD nuevo contra un gateway viejo falla en silencio; esto lo delata."""
    with TestClient(gateway_module.app) as client:
        health = client.get("/health").json()
    assert "music_stream" in health["features"]
    assert "wakeword" in health["features"]
    assert health["music"] == "off"  # sin JARVIS_NAVIDROME_URL


def test_hud_is_served_by_the_gateway(tmp_path, monkeypatch):
    """El HUD viaja con el servidor: una copia suelta no puede quedarse atrás."""
    hud = tmp_path / "index.html"
    hud.write_text('<!doctype html><section id="player"></section>', encoding="utf-8")
    monkeypatch.setattr(gateway_module.settings, "hud_path", str(hud))

    with TestClient(gateway_module.app) as client:
        response = client.get("/hud")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # Sin no-store el navegador seguiría con la versión anterior tras actualizar.
    assert response.headers["cache-control"] == "no-store"
    assert 'id="player"' in response.text


def test_hud_reports_a_clear_error_when_missing(monkeypatch):
    monkeypatch.setattr(gateway_module.settings, "hud_path", "/no/existe.html")
    with TestClient(gateway_module.app) as client:
        response = client.get("/hud")
    assert response.status_code == 404
    assert "no/existe.html" in response.json()["detail"]

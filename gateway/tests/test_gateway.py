"""Pruebas de integración del servicio principal sin consumir un LLM real."""

from fastapi.testclient import TestClient
from jarvis_core.agent.orchestrator import AgentReply
from jarvis_gateway import app as gateway_module


class FakeOrchestrator:
    async def send(self, message: str, on_text_delta=None, confirm=None) -> AgentReply:
        if on_text_delta is not None:
            await on_text_delta("eco:")
            await on_text_delta(message)
        return AgentReply(
            text=f"eco:{message}",
            tools_used=["system_info"],
            emotion="focused",
        )


class ApprovalOrchestrator:
    async def send(self, message: str, on_text_delta=None, confirm=None) -> AgentReply:
        assert confirm is not None
        approved = await confirm(
            "install_package",
            {"manager": "pip", "package": "requests"},
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


async def _fake_get(session_id: str) -> FakeOrchestrator:
    return FakeOrchestrator()


async def _approval_get(session_id: str) -> ApprovalOrchestrator:
    return ApprovalOrchestrator()


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

        monkeypatch.setattr(gateway_module.sessions, "get", _approval_get)
        with client.websocket_connect(
            "/ws/approval-hub?token=ci-test-key"
        ) as websocket:
            assert websocket.receive_json() == {"type": "state", "state": "idle"}
            websocket.send_text("instala requests")
            assert websocket.receive_json() == {"type": "state", "state": "listening"}
            assert websocket.receive_json() == {"type": "state", "state": "thinking"}
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

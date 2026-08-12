"""Pruebas de integración del servicio principal sin consumir un LLM real."""

from fastapi.testclient import TestClient

from jarvis_core.agent.orchestrator import AgentReply
from jarvis_gateway import app as gateway_module


class FakeOrchestrator:
    async def send(self, message: str, on_text_delta=None) -> AgentReply:
        if on_text_delta is not None:
            await on_text_delta("eco:")
            await on_text_delta(message)
        return AgentReply(
            text=f"eco:{message}",
            tools_used=["system_info"],
            emotion="focused",
        )


async def _fake_get(session_id: str) -> FakeOrchestrator:
    return FakeOrchestrator()


def test_gateway_health_auth_chat_and_websocket(monkeypatch):
    monkeypatch.setattr(gateway_module.sessions, "get", _fake_get)

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
            assert websocket.receive_json() == {"type": "reply_delta", "delta": "estado"}
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

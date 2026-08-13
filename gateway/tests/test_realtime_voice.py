"""Pruebas del broker Realtime sin abrir conexiones ni consumir la API."""

from types import SimpleNamespace

import pytest
from jarvis_core.config import Settings
from jarvis_gateway.realtime_voice import RealtimeVoiceBroker, RealtimeVoiceError


class FakeClientSecrets:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value="ek_test", expires_at=1_800_000_000)


def broker(tmp_path, *, enabled=True, daily_sessions=1):
    secrets = FakeClientSecrets()
    client = SimpleNamespace(
        realtime=SimpleNamespace(client_secrets=secrets)
    )
    settings = Settings(
        openai_realtime_enabled=enabled,
        openai_responses_api_key="sk-test" if enabled else "",
        openai_usage_db_path=str(tmp_path / "usage.db"),
        openai_realtime_daily_sessions=daily_sessions,
    )
    return RealtimeVoiceBroker(settings, client=client), secrets


async def test_ephemeral_secret_uses_audio_only_short_session(tmp_path):
    instance, secrets = broker(tmp_path)

    result = await instance.create_client_secret()

    assert result["value"] == "ek_test"
    assert result["model"] == "gpt-realtime-2.1-mini"
    assert instance.sessions_today() == 1
    request = secrets.calls[0]
    assert request["expires_after"] == {"anchor": "created_at", "seconds": 45}
    assert request["session"]["output_modalities"] == ["audio"]
    assert request["session"]["audio"]["output"]["voice"] == "marin"


async def test_daily_limit_blocks_new_session_before_api_call(tmp_path):
    instance, secrets = broker(tmp_path)
    await instance.create_client_secret()

    with pytest.raises(RealtimeVoiceError, match="límite diario"):
        await instance.create_client_secret()

    assert len(secrets.calls) == 1
    assert instance.status()["enabled"] is False


async def test_disabled_realtime_never_calls_api(tmp_path):
    instance, secrets = broker(tmp_path, enabled=False)

    with pytest.raises(RealtimeVoiceError, match="no está configurada"):
        await instance.create_client_secret()

    assert not secrets.calls

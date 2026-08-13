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


# ── Techo de gasto ───────────────────────────────────────────────────────────


def budget_broker(tmp_path, **overrides):
    settings = Settings(
        openai_realtime_enabled=True,
        openai_responses_api_key="sk-test",
        openai_usage_db_path=str(tmp_path / "usage.db"),
        openai_realtime_daily_sessions=0,  # sin límite por sesiones
        **overrides,
    )
    return RealtimeVoiceBroker(settings, client=SimpleNamespace())


def test_cost_uses_the_configured_audio_rates(tmp_path):
    broker = budget_broker(tmp_path)
    # 1 M de tokens de audio de entrada sin caché = la tarifa completa.
    assert broker.estimate_cost(
        {"input_tokens": 1_000_000, "input_audio_tokens": 1_000_000}
    ) == pytest.approx(10.0)
    assert broker.estimate_cost(
        {"output_tokens": 1_000_000, "output_audio_tokens": 1_000_000}
    ) == pytest.approx(20.0)


def test_cached_tokens_are_not_charged_twice(tmp_path):
    """OpenAI informa los cacheados DENTRO de los de entrada.

    Tarificarlos aparte multiplicaría la cuenta por más de treinta en una
    conversación larga, que es justo cuando la caché más pesa.
    """
    broker = budget_broker(tmp_path)
    usage = {
        "input_tokens": 1_000_000,
        "input_audio_tokens": 1_000_000,
        "cached_tokens": 900_000,
    }
    # 100k frescos a 10 $/M + 900k cacheados a 0,30 $/M.
    assert broker.estimate_cost(usage) == pytest.approx(1.0 + 0.27)


def test_cached_tokens_cannot_exceed_the_input_they_belong_to(tmp_path):
    broker = budget_broker(tmp_path)
    usage = {
        "input_tokens": 1000,
        "input_audio_tokens": 1000,
        "cached_tokens": 5000,  # dato incoherente del proveedor
    }
    # Se acota en lugar de producir un coste negativo.
    assert broker.estimate_cost(usage) >= 0


def test_non_audio_tokens_are_charged_as_text(tmp_path):
    broker = budget_broker(tmp_path)
    usage = {"input_tokens": 1_000_000, "input_audio_tokens": 0}
    assert broker.estimate_cost(usage) == pytest.approx(0.60)


def test_budget_blocks_new_sessions_once_the_ceiling_is_reached(tmp_path):
    broker = budget_broker(tmp_path, realtime_daily_budget_usd=0.05)
    assert broker.budget_available()

    # 4000 tokens de audio de salida = 0,08 $, por encima del techo.
    broker.record_usage({"output_tokens": 4000, "output_audio_tokens": 4000})
    assert broker.spend_today() == pytest.approx(0.08)
    assert not broker.budget_available()


def test_zero_budget_means_no_ceiling(tmp_path):
    broker = budget_broker(tmp_path, realtime_daily_budget_usd=0.0)
    broker.record_usage({"output_tokens": 5_000_000, "output_audio_tokens": 5_000_000})
    assert broker.budget_available()


def test_spend_survives_a_restart(tmp_path):
    first = budget_broker(tmp_path, realtime_daily_budget_usd=0.05)
    first.record_usage({"output_tokens": 4000, "output_audio_tokens": 4000})
    # Un reinicio no debe borrar el gasto del día.
    second = budget_broker(tmp_path, realtime_daily_budget_usd=0.05)
    assert not second.budget_available()


def test_status_never_exposes_spending(tmp_path):
    broker = budget_broker(tmp_path, realtime_daily_budget_usd=1.0)
    broker.record_usage({"output_tokens": 4000, "output_audio_tokens": 4000})
    serialized = str(broker.status())
    assert "spend" not in serialized and "budget" not in serialized
    assert "usd" not in serialized.lower()

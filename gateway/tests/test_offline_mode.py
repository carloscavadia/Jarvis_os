"""El modo offline como promesa comprobable.

Montar JARVIS sin que nada salga de la red ya era posible: proveedor local, voz
local, palabra de activación local. Pero verificarlo exigía auditar diez
variables de entorno, y bastaba olvidar una para que el audio o los hechos
personales salieran de casa sin que nadie se enterara. Aquí es un interruptor, y
`/ready` dice si de verdad se está cumpliendo.
"""

import pytest
from jarvis_core.config import Settings
from jarvis_core.offline import (
    apply_offline_mode,
    is_private_endpoint,
    offline_violations,
)

LOCAL = "http://192.168.68.50:11434/v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.68.50:11434/v1",
        "http://10.0.0.5",
        "http://172.16.3.9:8080",
        "http://localhost:11434",
        "http://127.0.0.1:8080",
        "http://ollama.local",
        "http://nas.lan:5000",
        "",
    ],
)
def test_reconoce_lo_que_esta_dentro_de_casa(url):
    assert is_private_endpoint(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com",
        "https://integrate.api.nvidia.com/v1",
        "https://n8n.example.com/webhook/x",
        "http://8.8.8.8",
        "http://nas.midominio.com",
    ],
)
def test_lo_demas_se_considera_fuera(url):
    """Cerrado por defecto: lo que no se demuestra interno, no lo es."""
    assert is_private_endpoint(url) is False


def test_un_dominio_propio_se_puede_declarar():
    assert is_private_endpoint("http://nas.midominio.com", ("nas.midominio.com",)) is True


def test_apaga_lo_que_sale_a_internet(monkeypatch):
    monkeypatch.setenv("JARVIS_OFFLINE", "true")
    monkeypatch.setenv("JARVIS_OPENAI_REALTIME_ENABLED", "true")
    monkeypatch.setenv("JARVIS_REALTIME_CONVERSATION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_INTERNET_ACCESS_ENABLED", "true")

    settings = Settings.from_env()

    # El audio de OpenAI y la web pública se apagan solos, aunque estén pedidos.
    assert settings.openai_realtime_enabled is False
    assert settings.realtime_conversation_enabled is False
    assert settings.internet_access_enabled is False


def test_la_voz_local_no_se_toca(monkeypatch):
    """Offline no es «sin voz»: Whisper y Kokoro corren en tu servidor."""
    monkeypatch.setenv("JARVIS_OFFLINE", "true")
    monkeypatch.setenv("JARVIS_VOICE_ENABLED", "true")
    monkeypatch.setenv("JARVIS_WAKEWORD_ENABLED", "true")

    settings = Settings.from_env()

    assert settings.voice_enabled is True
    assert settings.wakeword_enabled is True


def test_los_embeddings_remotos_caen_al_modelo_local():
    settings = Settings(
        offline_mode=True,
        embeddings_provider="openai",
        openai_base_url="https://integrate.api.nvidia.com/v1",
    )
    apply_offline_mode(settings)
    assert settings.embeddings_provider == "local"


def test_un_endpoint_interno_si_se_respeta():
    """NVIDIA no, pero un servidor de embeddings en tu LAN sí es offline."""
    settings = Settings(
        offline_mode=True, embeddings_provider="openai", openai_base_url=LOCAL
    )
    apply_offline_mode(settings)
    assert settings.embeddings_provider == "openai"


def test_un_proveedor_en_la_nube_se_reporta_como_incumplimiento():
    settings = Settings(offline_mode=True, llm_provider="anthropic")
    problemas = offline_violations(settings)
    assert len(problemas) == 1
    # El mensaje tiene que decir cómo arreglarlo, no solo que está mal.
    assert "JARVIS_OPENAI_BASE_URL" in problemas[0]


def test_una_configuracion_offline_correcta_no_tiene_incumplimientos():
    settings = Settings(
        offline_mode=True, llm_provider="openai", openai_base_url=LOCAL
    )
    assert offline_violations(settings) == []


def test_un_n8n_externo_se_delata():
    settings = Settings(
        offline_mode=True,
        llm_provider="openai",
        openai_base_url=LOCAL,
        n8n_webhook_url="https://n8n.example.com/webhook/x",
    )
    assert any("n8n" in p for p in offline_violations(settings))


def test_un_n8n_autoalojado_no_es_un_problema():
    settings = Settings(
        offline_mode=True,
        llm_provider="openai",
        openai_base_url=LOCAL,
        n8n_webhook_url="http://192.168.68.20:5678/webhook/x",
    )
    assert offline_violations(settings) == []


def test_sin_modo_offline_no_se_impone_nada():
    settings = Settings(offline_mode=False, llm_provider="anthropic")
    assert offline_violations(settings) == []


# ── Y lo que lo hace comprobable de verdad: el propio gateway lo dice ──

from fastapi.testclient import TestClient  # noqa: E402
from jarvis_gateway import app as gateway_module  # noqa: E402

KEY = {"X-Jarvis-Key": "ci-test-key"}


def test_ready_confirma_que_el_modo_offline_se_cumple(monkeypatch):
    monkeypatch.setattr(
        gateway_module.settings, "offline_mode", True, raising=False
    )
    monkeypatch.setattr(gateway_module.settings, "llm_provider", "openai")
    monkeypatch.setattr(gateway_module.settings, "openai_base_url", LOCAL)
    monkeypatch.setattr(gateway_module.settings, "openai_api_key", "ollama")
    monkeypatch.setattr(gateway_module.settings, "openai_model", "llama3.1")

    with TestClient(gateway_module.app) as client:
        respuesta = client.get("/ready")

    assert respuesta.status_code == 200, respuesta.json()
    cuerpo = respuesta.json()
    assert cuerpo["offline"] is True
    assert "offline_violations" not in cuerpo


def test_ready_delata_lo_que_seguiria_saliendo(monkeypatch):
    monkeypatch.setattr(
        gateway_module.settings, "offline_mode", True, raising=False
    )
    monkeypatch.setattr(gateway_module.settings, "llm_provider", "anthropic")

    with TestClient(gateway_module.app) as client:
        cuerpo = client.get("/ready").json()

    assert cuerpo["offline"] is False
    assert cuerpo["offline_violations"], "prometer offline sin comprobarlo no vale"


def test_sin_modo_offline_ready_no_habla_del_tema():
    with TestClient(gateway_module.app) as client:
        cuerpo = client.get("/ready").json()
    assert "offline" not in cuerpo


def test_health_lo_muestra_de_un_vistazo(monkeypatch):
    monkeypatch.setattr(
        gateway_module.settings, "offline_mode", True, raising=False
    )
    with TestClient(gateway_module.app) as client:
        assert client.get("/health").json()["offline"] == "on"

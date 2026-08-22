"""Tests de la herramienta de correo SMTP.

El primero de estos tests existe por un fallo concreto: `SendEmailTool` definía
`execute` en vez de `run`, el método abstracto de `Tool`. La clase quedaba sin
poder instanciarse y, como se registra dentro de `build_default_registry`, el
gateway se quedaba **sin ninguna herramienta** en cuanto alguien configuraba
SMTP. La suite pasaba porque CI no define `JARVIS_SMTP_*` y esa rama nunca se
recorría: por eso aquí se construye el registro con SMTP puesto a mano.
"""

import asyncio
import smtplib

from jarvis_core.config import Settings
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tools.builtin import build_default_registry
from jarvis_core.tools.builtin.email_tool import SendEmailTool


def _tool(**overrides) -> SendEmailTool:
    kwargs = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "jarvis@example.com",
        "smtp_pass": "secreto",
        "default_to": "carlos@example.com",
    }
    kwargs.update(overrides)
    return SendEmailTool(**kwargs)


def test_la_herramienta_es_instanciable_y_cumple_el_contrato_de_tool():
    tool = _tool()
    assert tool.name == "send_email"
    assert asyncio.iscoroutinefunction(tool.run)
    assert tool.definition()["input_schema"]["required"] == ["subject", "body"]


def test_el_registro_completo_se_construye_con_smtp_configurado(tmp_path, monkeypatch):
    """La regresión de verdad: con SMTP puesto, `build_default_registry` no revienta."""
    monkeypatch.setenv("JARVIS_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("JARVIS_SMTP_USER", "jarvis@example.com")
    monkeypatch.setenv("JARVIS_SMTP_PASS", "secreto")
    monkeypatch.setenv("JARVIS_EMAIL_TO", "carlos@example.com")
    registry = build_default_registry(
        Settings.from_env(), MemoryStore(str(tmp_path / "memoria.db"))
    )
    assert "send_email" in {d["name"] for d in registry.definitions()}


def test_enviar_un_correo_exige_aprobacion_humana():
    """Es una acción de escritura hacia fuera; el HUD debe poder vetarla."""
    assert _tool().requires_confirmation is True


async def test_envia_por_starttls_y_no_bloquea_el_bucle_de_eventos(monkeypatch):
    enviados: list[tuple[str, str, str]] = []
    hilo_del_envio: list[object] = []

    import threading

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host, self.port, self.starttls_llamado = host, port, False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            self.starttls_llamado = True

        def login(self, user, password):
            self.credenciales = (user, password)

        def send_message(self, msg):
            hilo_del_envio.append(threading.current_thread())
            assert self.starttls_llamado, "se envió sin cifrar el canal"
            cuerpo = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
            enviados.append((msg["To"], msg["Subject"], cuerpo))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result = await _tool().run(subject="Recordatorio", body="Cuerpo del aviso")

    assert not result.is_error
    assert enviados == [("carlos@example.com", "Recordatorio", "Cuerpo del aviso")]
    # `smtplib` es síncrono: si corriera en el hilo del bucle, un servidor lento
    # congelaría WebSockets, voz y HUD durante todo el timeout.
    assert hilo_del_envio[0] is not threading.current_thread()


async def test_el_puerto_465_usa_smtps_directo(monkeypatch):
    usados: list[str] = []

    class FakeSMTPS:
        def __init__(self, host, port, timeout=None):
            usados.append("SMTP_SSL")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPS)
    result = await _tool(smtp_port=465).run(subject="s", body="b")

    assert not result.is_error
    assert usados == ["SMTP_SSL"]


async def test_sin_destinatario_avisa_en_vez_de_fallar():
    result = await _tool(default_to="").run(subject="s", body="b")
    assert result.is_error
    assert "JARVIS_EMAIL_TO" in result.content


async def test_sin_credenciales_avisa_en_vez_de_fallar():
    result = await _tool(smtp_pass="").run(subject="s", body="b")
    assert result.is_error
    assert "JARVIS_SMTP_HOST" in result.content


async def test_un_fallo_del_servidor_se_devuelve_como_error_de_herramienta(monkeypatch):
    def explota(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"credenciales rechazadas")

    monkeypatch.setattr(smtplib, "SMTP", explota)
    result = await _tool().run(subject="s", body="b")

    assert result.is_error
    assert "Error enviando correo SMTP" in result.content


def test_la_aprobacion_del_hud_muestra_lo_necesario_para_decidir():
    """Sin destinatario ni asunto visibles, el diálogo salía en blanco: no había
    nada que aprobar. El cuerpo va por tamaño, igual que `content`."""
    from jarvis_gateway.app import _approval_summary, _public_approval_arguments

    argumentos = {"to": "carlos@example.com", "subject": "Aviso", "body": "x" * 500}

    resumen = _approval_summary("send_email", argumentos)
    assert "carlos@example.com" in resumen and "Aviso" in resumen

    publico = _public_approval_arguments(argumentos)
    assert publico == {
        "to": "carlos@example.com",
        "subject": "Aviso",
        "body_bytes": 500,
    }
    assert "body" not in publico

"""Que JARVIS averigüe en vez de rendirse.

El caso real: «conéctate a mi Proxmox, las claves ya están en el .env». JARVIS no
supo qué hacer, y no fue un fallo de razonamiento: no existía ninguna herramienta
de Proxmox —el HUD la pintaba desde un endpoint del gateway— y tampoco tenía
forma de saber qué tenía configurado. Tenía las credenciales delante y las manos
atadas, así que dijo «no puedo»: la peor respuesta, porque no dice qué falta.
"""

import asyncio
import json
import urllib.error

import pytest
from jarvis_core.config import Settings
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin.introspection import register_introspection_tool
from jarvis_core.tools.builtin.proxmox import ProxmoxClient, register_proxmox_tools

SECRETO = "s3creto-que-no-debe-salir-jamas"


def _registro(settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()
    register_proxmox_tools(registry, settings)
    register_introspection_tool(registry, settings)
    return registry


def _describir(settings: Settings, capacidad: str = "") -> str:
    return asyncio.run(
        _registro(settings).execute("describe_capabilities", {"capability": capacidad})
    ).content


# ── Saber qué tiene y qué le falta ──

def test_dice_exactamente_que_variable_falta(monkeypatch):
    """«No tengo acceso a Proxmox» no sirve. «Falta esta variable» sí."""
    monkeypatch.setenv("JARVIS_PROXMOX_URL", "https://192.168.68.201:8006")
    monkeypatch.setenv("JARVIS_PROXMOX_TOKEN_ID", "root@pam!jarvis")
    monkeypatch.delenv("JARVIS_PROXMOX_TOKEN_SECRET", raising=False)

    informe = _describir(Settings(), "proxmox")

    assert "JARVIS_PROXMOX_TOKEN_SECRET" in informe
    # Y no acusa a las que sí están puestas.
    assert "JARVIS_PROXMOX_TOKEN_ID" not in informe
    assert "API Tokens" in informe, "hay que decir dónde se consigue"


def test_con_todo_puesto_se_declara_disponible(monkeypatch):
    for variable, valor in (
        ("JARVIS_PROXMOX_URL", "https://192.168.68.201:8006"),
        ("JARVIS_PROXMOX_TOKEN_ID", "root@pam!jarvis"),
        ("JARVIS_PROXMOX_TOKEN_SECRET", SECRETO),
    ):
        monkeypatch.setenv(variable, valor)

    informe = _describir(Settings(), "proxmox")

    assert informe.startswith("✅")
    assert "proxmox_status" in informe


def test_nunca_revela_el_valor_de_un_secreto(monkeypatch):
    """Su respuesta acaba en un chat, en un log o leída en voz alta."""
    monkeypatch.setenv("JARVIS_PROXMOX_TOKEN_SECRET", SECRETO)
    monkeypatch.setenv("JARVIS_SMTP_PASS", SECRETO)

    completo = _describir(Settings())

    assert SECRETO not in completo


def test_distingue_apagado_de_inexistente(monkeypatch):
    monkeypatch.delenv("JARVIS_INTERNET_ACCESS_ENABLED", raising=False)
    apagada = _describir(Settings(internet_access_enabled=False), "internet")
    assert "apagada" in apagada
    assert "JARVIS_INTERNET_ACCESS_ENABLED" in apagada


def test_el_inventario_lista_lo_que_hay_registrado():
    completo = _describir(Settings())
    assert "Herramientas registradas ahora mismo" in completo
    assert "proxmox_status" in completo


def test_una_capacidad_desconocida_sugiere_las_que_conoce():
    resultado = asyncio.run(
        _registro(Settings()).execute("describe_capabilities", {"capability": "teletransporte"})
    )
    assert resultado.is_error
    assert "proxmox" in resultado.content


# ── Y tener manos de verdad ──

def test_sin_credenciales_la_herramienta_dice_que_hace_falta():
    registry = _registro(Settings())
    resultado = asyncio.run(registry.execute("proxmox_status", {}))
    assert resultado.is_error
    assert "JARVIS_PROXMOX_TOKEN_ID" in resultado.content


def _cliente_falso(respuesta=None, error=None):
    cliente = ProxmoxClient("https://pve.local:8006", "root@pam!j", SECRETO, False)

    async def get(path):
        if error:
            raise error
        return respuesta
    cliente.get = get
    return cliente


def test_el_estado_de_los_nodos_se_lee_en_lenguaje_natural():
    from jarvis_core.tools.builtin.proxmox import ProxmoxStatusTool

    herramienta = ProxmoxStatusTool(_cliente_falso([{
        "node": "pve", "status": "online", "cpu": 0.076,
        "mem": 30 * 2**30, "maxmem": 32 * 2**30,
        "disk": 70 * 2**30, "maxdisk": 500 * 2**30, "uptime": 7200,
    }]))

    contenido = asyncio.run(herramienta.run()).content

    assert "pve" in contenido and "online" in contenido
    assert "7.6%" in contenido
    assert "93.8%" in contenido      # 30 de 32 GiB
    assert "2 h" in contenido


def test_las_maquinas_se_devuelven_estructuradas_para_el_pizarron():
    from jarvis_core.tools.builtin.proxmox import ProxmoxGuestsTool

    herramienta = ProxmoxGuestsTool(_cliente_falso([
        {"vmid": 100, "name": "jarvis", "type": "lxc", "status": "running", "node": "pve", "maxmem": 2 * 2**30},
        {"vmid": 101, "name": "nas", "type": "qemu", "status": "stopped", "node": "pve", "maxmem": 8 * 2**30},
    ]))

    datos = json.loads(asyncio.run(herramienta.run()).content)

    assert datos["total"] == 2
    assert {g["type"] for g in datos["guests"]} == {"LXC", "VM"}
    assert datos["guests"][0]["mem_gib"] == 2.0


def test_se_pueden_pedir_solo_las_encendidas():
    from jarvis_core.tools.builtin.proxmox import ProxmoxGuestsTool

    herramienta = ProxmoxGuestsTool(_cliente_falso([
        {"vmid": 100, "name": "a", "type": "lxc", "status": "running", "node": "pve", "maxmem": 0},
        {"vmid": 101, "name": "b", "type": "qemu", "status": "stopped", "node": "pve", "maxmem": 0},
    ]))

    datos = json.loads(asyncio.run(herramienta.run(only_running=True)).content)

    assert [g["name"] for g in datos["guests"]] == ["a"]


@pytest.mark.parametrize("codigo,esperado", [(401, "credenciales"), (403, "credenciales"), (500, "HTTP 500")])
def test_un_token_rechazado_se_explica_en_vez_de_reventar(codigo, esperado):
    from jarvis_core.tools.builtin.proxmox import ProxmoxStatusTool

    error = urllib.error.HTTPError("u", codigo, "x", {}, None)
    resultado = asyncio.run(ProxmoxStatusTool(_cliente_falso(error=error)).run())

    assert resultado.is_error
    assert esperado in resultado.content


def test_las_herramientas_existen_aunque_falten_credenciales():
    """Así JARVIS puede decir que la capacidad existe y qué falta, en vez de
    comportarse como si no existiera. Ese fue justo el fallo original."""
    assert "proxmox_status" in _registro(Settings()).names()


# ── El método ──

def test_el_prompt_le_prohibe_rendirse_sin_mirar():
    prompt = Settings().system_prompt()
    assert "describe_capabilities" in prompt
    assert "no respondas que no puedes" in prompt
    assert "Rendirse sin haber mirado" in prompt
    # Y le dice qué hacer después de averiguarlo.
    assert "learn_skill" in prompt

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
    assert "Herramientas registradas" in completo
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


def test_el_prompt_le_dice_que_pida_lo_que_falta_formalmente():
    """Mencionarlo de pasada no basta: se lee y se olvida."""
    prompt = Settings().system_prompt()
    assert "request_from_user" in prompt
    assert "Nunca pidas que te escriban un secreto en el chat" in prompt


def test_el_prompt_le_prohibe_quedarse_parado_esperando():
    """Bloquearse por una credencial cuando el resto era posible es rendirse."""
    assert "rendirse con otro nombre" in Settings().system_prompt()


# ── Pedir lo que hace falta ──

def _pedir(**campos):
    from jarvis_core.tools.builtin.requests_tool import RequestFromUserTool

    return asyncio.run(RequestFromUserTool().run(**campos))


def test_una_peticion_valida_se_acepta():
    resultado = _pedir(
        kind="credential",
        what="el token de API de Proxmox",
        why="para leer el estado del servidor",
        where="JARVIS_PROXMOX_TOKEN_SECRET en el .env",
    )
    assert not resultado.is_error


def test_pedir_una_credencial_sin_decir_donde_se_pone_se_rechaza():
    """Si no, el usuario se queda con el problema pero sin la solución."""
    resultado = _pedir(kind="credential", what="un token", why="para algo")
    assert resultado.is_error
    assert "dónde se pone" in resultado.content


def test_una_peticion_sin_para_que_se_rechaza():
    """El usuario decide mejor si sabe qué desbloquea."""
    assert _pedir(kind="setting", what="una URL", why="  ").is_error


def test_una_decision_sin_alternativas_no_es_una_decision():
    assert _pedir(kind="decision", what="¿cómo lo hago?", why="hay dos formas").is_error


def test_una_decision_con_alternativas_si():
    assert not _pedir(
        kind="decision", what="¿cómo consulto Proxmox?", why="hay dos vías",
        options=["Token de API", "Por SSH"],
    ).is_error


def test_el_tipo_tiene_que_ser_uno_de_los_conocidos():
    resultado = _pedir(kind="telepatia", what="x", why="y")
    assert resultado.is_error
    assert "credential" in resultado.content


def test_la_descripcion_prohibe_pedir_secretos_por_el_chat():
    """Un token tecleado en la conversación entra en el historial y viaja al
    modelo en cada turno posterior."""
    from jarvis_core.tools.builtin.requests_tool import RequestFromUserTool

    descripcion = RequestFromUserTool().definition()["description"]
    assert "nunca pidas que te lo escriban en el chat" in descripcion


def test_el_gateway_convierte_la_peticion_en_algo_presentable():
    from jarvis_gateway.app import _capability_request

    presentable = _capability_request("request_from_user", {
        "kind": "credential", "what": "El token de Proxmox",
        "why": "para leer el servidor", "where": "JARVIS_PROXMOX_TOKEN_SECRET",
    })

    assert presentable["kind"] == "credential"
    assert presentable["where"] == "JARVIS_PROXMOX_TOKEN_SECRET"


def test_otras_herramientas_no_generan_tarjeta():
    from jarvis_gateway.app import _capability_request

    assert _capability_request("create_file", {"path": "x"}) is None


# ── Conocerse a sí mismo ──

class _HabilidadFalsa:
    def __init__(self, name, description, usage_count, success_rate, enabled=True):
        self.name = name
        self.title = name
        self.description = description
        self.skill_type = "instruction"
        self.usage_count = usage_count
        self.success_rate = success_rate
        self.enabled = enabled


class _AlmacenHabilidades:
    def __init__(self, habilidades):
        self._h = habilidades

    def list_all(self):
        return self._h


def _mirarse(settings=None, scope="all", **piezas):
    from jarvis_core.tools.builtin.introspection import DescribeCapabilitiesTool

    registry = ToolRegistry()
    register_proxmox_tools(registry, settings or Settings())
    herramienta = DescribeCapabilitiesTool(settings or Settings(), registry, **piezas)
    return asyncio.run(herramienta.run(scope=scope)).content


def test_sabe_con_que_cerebro_piensa():
    informe = _mirarse(Settings(persona_name="JARVIS", model="claude-opus-5"), "identity")
    assert "JARVIS" in informe
    assert "claude-opus-5" in informe


def test_conoce_las_reglas_de_la_casa_vigentes():
    informe = _mirarse(Settings(persona_extra="Trátame de usted."), "identity")
    assert "Trátame de usted." in informe


def test_sabe_cuando_esta_en_modo_offline():
    assert "offline" in _mirarse(Settings(offline_mode=True), "identity")


def test_conoce_sus_propios_limites():
    """Saber lo que no puede hacer por diseño evita prometer de más."""
    informe = _mirarse(Settings(max_tool_iterations=12), "limits")
    assert "12 pasos" in informe
    assert "aprobación del usuario" in informe
    assert "no puedes concedértela" in informe


def test_consulta_sus_habilidades_aprendidas():
    informe = _mirarse(scope="skills", skills=_AlmacenHabilidades([
        _HabilidadFalsa("resumen_diario", "Resume el correo de la mañana", 7, 0.857),
    ]))
    assert "resumen_diario" in informe
    assert "usada 7 veces" in informe
    assert "86%" in informe, "la fiabilidad importa tanto como la existencia"


def test_una_habilidad_sin_estrenar_no_finge_fiabilidad():
    informe = _mirarse(scope="skills", skills=_AlmacenHabilidades([
        _HabilidadFalsa("nueva", "Algo", 0, 1.0),
    ]))
    assert "sin usar aún" in informe
    assert "100%" not in informe


def test_sin_habilidades_sugiere_aprender_en_vez_de_callar():
    informe = _mirarse(scope="skills", skills=_AlmacenHabilidades([]))
    assert "learn_skill" in informe


def test_sabe_a_quien_puede_delegar():
    from jarvis_core.agent.subagents import DEFAULT_SUBAGENTS

    informe = _mirarse(scope="skills", subagents=DEFAULT_SUBAGENTS[:2])
    assert "ESPECIALISTAS" in informe
    assert DEFAULT_SUBAGENTS[0].name in informe


def test_sabe_cuanto_recuerda_pero_no_lo_vuelca(tmp_path):
    from jarvis_core.memory.store import MemoryStore

    memoria = MemoryStore(str(tmp_path / "m.db"))
    memoria.remember("coche", "un Tesla Model 3 azul")

    informe = _mirarse(scope="memory", memory=memoria)

    assert "1 hechos" in informe or "Recuerdas 1" in informe
    assert "coche" in informe                      # el tema, sí
    assert "Tesla Model 3 azul" not in informe     # el contenido, no: para eso está recall
    assert "recall" in informe


def test_un_almacen_de_habilidades_roto_no_tumba_la_introspeccion():
    class Roto:
        def list_all(self):
            raise RuntimeError("base bloqueada")

    informe = _mirarse(scope="skills", skills=Roto())
    assert "No pude leer" in informe


def test_el_inventario_completo_reune_todas_las_secciones(tmp_path):
    from jarvis_core.memory.store import MemoryStore

    informe = _mirarse(
        scope="all",
        skills=_AlmacenHabilidades([_HabilidadFalsa("x", "y", 1, 1.0)]),
        memory=MemoryStore(str(tmp_path / "m.db")),
    )
    for seccion in ("QUIÉN ERES", "LO QUE PUEDES HACER", "HABILIDADES APRENDIDAS", "TUS LÍMITES"):
        assert seccion in informe, f"falta la sección {seccion}"


def test_un_ambito_desconocido_se_reporta():
    from jarvis_core.tools.builtin.introspection import DescribeCapabilitiesTool

    resultado = asyncio.run(
        DescribeCapabilitiesTool(Settings(), ToolRegistry()).run(scope="telepatia")
    )
    assert resultado.is_error

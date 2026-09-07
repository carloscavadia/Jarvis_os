"""Tests del nodo: contrato, confinamiento y ejecución.

El grueso está en lo que el nodo **se niega** a hacer. Un nodo que ejecuta lo que le
piden es fácil; el valor está en que siga negándose cuando la petición viene bien
formada y con mala intención.
"""

import json
import sys

import pytest
from jarvis_node.agent import NodeAgent
from jarvis_node.capabilities import CapabilityError, NodeCapabilities
from jarvis_node.config import ConfigError, load_capabilities
from jarvis_node.protocol import ProtocolError, Request, Response


@pytest.fixture
def caps(tmp_path):
    (tmp_path / "lectura").mkdir()
    (tmp_path / "escritura").mkdir()
    return NodeCapabilities(
        node_id="pc-prueba",
        read_roots=[tmp_path / "lectura", tmp_path / "escritura"],
        write_roots=[tmp_path / "escritura"],
        allowed_executables=["echo"],
        allow_writes=True,
    )


@pytest.fixture
def agent(caps):
    return NodeAgent(caps)


# --- protocolo ----------------------------------------------------------


def test_una_peticion_sin_id_se_rechaza():
    """Sin id no hay a quién contestar, y una respuesta huérfana confunde al gateway."""
    with pytest.raises(ProtocolError):
        Request.from_json(json.dumps({"op": "system_info"}))


def test_una_peticion_sin_op_se_rechaza():
    with pytest.raises(ProtocolError):
        Request.from_json(json.dumps({"id": "1"}))


def test_un_payload_que_no_es_json_se_rechaza():
    with pytest.raises(ProtocolError):
        Request.from_json("{no es json")


def test_params_debe_ser_un_objeto():
    with pytest.raises(ProtocolError):
        Request.from_json(json.dumps({"id": "1", "op": "x", "params": [1, 2]}))


def test_ida_y_vuelta_de_una_peticion():
    original = Request.new("read_file", path="/tmp/x")
    copia = Request.from_json(original.to_json())
    assert copia.op == "read_file"
    assert copia.params == {"path": "/tmp/x"}
    assert copia.id == original.id


def test_una_denegacion_se_distingue_de_un_fallo():
    """El gateway necesita separar 'no te dejo' de 'lo intenté y salió mal'."""
    denegada = json.loads(Response.denied("1", "fuera de raíz").to_json())
    fallida = json.loads(Response.failure("1", "explotó").to_json())
    assert denegada["denied_reason"] == "fuera de raíz"
    assert "denied_reason" not in fallida


# --- confinamiento de rutas ---------------------------------------------


def test_una_ruta_fuera_de_las_raices_se_rechaza(caps):
    with pytest.raises(CapabilityError, match="fuera de las raíces"):
        caps.resolve_path("/etc/passwd")


def test_los_dos_puntos_no_escapan(caps, tmp_path):
    with pytest.raises(CapabilityError):
        caps.resolve_path(str(tmp_path / "lectura" / ".." / ".." / "fuera"))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks piden privilegios en Windows")
def test_un_enlace_simbolico_no_saca_del_confinamiento(caps, tmp_path):
    """Sin resolver enlaces antes de comparar, la confinación sería decorativa."""
    puente = tmp_path / "lectura" / "puente"
    puente.symlink_to("/etc")
    with pytest.raises(CapabilityError):
        caps.resolve_path(str(puente / "passwd"))


def test_las_rutas_protegidas_se_niegan_dentro_de_una_raiz(caps, tmp_path):
    """`.ssh` no es 'un archivo más': basta una lectura para perder las claves."""
    ssh = tmp_path / "lectura" / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("clave")
    with pytest.raises(CapabilityError, match="protegida"):
        caps.resolve_path(str(ssh / "id_rsa"))


def test_sin_raices_declaradas_no_se_lee_nada(tmp_path):
    vacio = NodeCapabilities(node_id="n")
    with pytest.raises(CapabilityError):
        vacio.resolve_path(str(tmp_path))


def test_escribir_donde_solo_se_puede_leer_se_rechaza(caps, tmp_path):
    caps.resolve_path(str(tmp_path / "lectura" / "x"), write=False)
    with pytest.raises(CapabilityError, match="fuera de las raíces"):
        caps.resolve_path(str(tmp_path / "lectura" / "x"), write=True)


def test_en_solo_lectura_no_se_escribe_en_ningun_sitio(tmp_path):
    solo_lectura = NodeCapabilities(
        node_id="n", read_roots=[tmp_path], write_roots=[tmp_path], allow_writes=False
    )
    with pytest.raises(CapabilityError, match="solo lectura"):
        solo_lectura.resolve_path(str(tmp_path / "x"), write=True)


# --- ejecutables ---------------------------------------------------------


def test_un_ejecutable_no_declarado_se_rechaza(caps):
    with pytest.raises(CapabilityError, match="no está en los ejecutables"):
        caps.check_executable(["curl", "https://example.com"])


def test_una_ruta_absoluta_no_rodea_la_lista(caps):
    """Comparar por nombre base: si no, `/bin/echo` esquivaría la comprobación."""
    assert caps.check_executable(["/bin/echo", "hola"]) == "echo"


def test_los_prohibidos_lo_estan_aunque_se_declaren(tmp_path):
    permisivo = NodeCapabilities(node_id="n", allowed_executables=["dd", "shutdown", "echo"])
    for prohibido in ("dd", "shutdown"):
        with pytest.raises(CapabilityError, match="prohibido"):
            permisivo.check_executable([prohibido])
    assert permisivo.check_executable(["echo"]) == "echo"


def test_el_sufijo_exe_no_esquiva_la_prohibicion(tmp_path):
    """En Windows los programas llevan `.exe`; sin normalizar, `dd.exe` pasaría."""
    permisivo = NodeCapabilities(node_id="n", allowed_executables=["dd.exe"])
    with pytest.raises(CapabilityError, match="prohibido"):
        permisivo.check_executable(["C:\\Windows\\System32\\dd.exe"])


def test_argv_debe_ser_una_lista_de_cadenas(caps):
    with pytest.raises(CapabilityError):
        caps.check_executable(["echo", {"malicioso": True}])


# --- manifiesto ----------------------------------------------------------


def test_el_manifiesto_solo_anuncia_lo_que_se_puede_hacer(tmp_path):
    minimo = NodeCapabilities(node_id="n", read_roots=[tmp_path])
    ops = set(minimo.manifest()["operations"])
    assert "run_command" not in ops
    assert "write_file" not in ops
    assert "system_info" in ops


def test_declarar_ejecutables_habilita_run_command(caps):
    assert "run_command" in caps.manifest()["operations"]


# --- despachador ---------------------------------------------------------


async def test_system_info_responde_sin_configurar_nada():
    agente = NodeAgent(NodeCapabilities(node_id="pelado"))
    respuesta = await agente.handle(Request.new("system_info"))
    assert respuesta.ok
    assert respuesta.result["node_id"] == "pelado"


async def test_una_operacion_desconocida_no_revienta(agent):
    respuesta = await agent.handle(Request.new("formatear_disco"))
    assert not respuesta.ok
    assert "desconocida" in respuesta.error


async def test_una_operacion_no_declarada_se_deniega(tmp_path):
    agente = NodeAgent(NodeCapabilities(node_id="n", read_roots=[tmp_path]))
    respuesta = await agente.handle(Request.new("run_command", argv=["echo", "hola"]))
    assert not respuesta.ok
    assert respuesta.denied_reason


async def test_un_parametro_desconocido_no_rompe_una_llamada_valida(agent, tmp_path):
    """Las operaciones aceptan `**_` a propósito: un gateway más nuevo que el nodo
    puede mandar campos que este todavía no entiende, y descartarlos es preferible a
    fallar. Lo que no puede es cambiar el resultado."""
    destino = tmp_path / "lectura" / "n.txt"
    destino.write_text("contenido")
    respuesta = await agent.handle(
        Request.new("read_file", path=str(destino), campo_del_futuro=123)
    )
    assert respuesta.ok
    assert respuesta.result["content"] == "contenido"


async def test_falta_un_parametro_obligatorio_y_lo_dice(agent):
    respuesta = await agent.handle(Request.new("read_file"))
    assert not respuesta.ok
    assert "vacía" in respuesta.denied_reason


async def test_un_payload_corrupto_no_produce_respuesta(agent):
    """Sin `id` no hay destinatario: publicar algo sería peor que callar."""
    assert await agent.handle_raw("{roto") is None


async def test_ciclo_completo_desde_json(agent):
    crudo = Request.new("system_info").to_json()
    respuesta = json.loads(await agent.handle_raw(crudo))
    assert respuesta["ok"] is True


# --- operaciones ---------------------------------------------------------


async def test_leer_y_escribir_dentro_de_las_raices(agent, tmp_path):
    destino = tmp_path / "escritura" / "nota.txt"
    escritura = await agent.handle(
        Request.new("write_file", path=str(destino), content="hola jarvis")
    )
    assert escritura.ok
    lectura = await agent.handle(Request.new("read_file", path=str(destino)))
    assert lectura.result["content"] == "hola jarvis"


async def test_leer_fuera_de_las_raices_se_deniega(agent):
    respuesta = await agent.handle(Request.new("read_file", path="/etc/passwd"))
    assert not respuesta.ok
    assert respuesta.denied_reason


async def test_la_lectura_se_recorta_y_lo_dice(caps, tmp_path):
    caps.max_output_bytes = 10
    grande = tmp_path / "lectura" / "grande.txt"
    grande.write_text("x" * 500)
    respuesta = await NodeAgent(caps).handle(Request.new("read_file", path=str(grande)))
    assert respuesta.result["truncated"] is True
    assert len(respuesta.result["content"]) == 10


async def test_listar_un_directorio(agent, tmp_path):
    (tmp_path / "lectura" / "a.txt").write_text("a")
    respuesta = await agent.handle(Request.new("list_directory", path=str(tmp_path / "lectura")))
    assert [e["name"] for e in respuesta.result["entries"]] == ["a.txt"]


async def test_run_command_ejecuta_lo_permitido(agent):
    respuesta = await agent.handle(Request.new("run_command", argv=["echo", "hola"]))
    assert respuesta.ok
    assert respuesta.result["exit_code"] == 0
    assert "hola" in respuesta.result["stdout"]


async def test_run_command_no_interpreta_operadores(agent):
    """argv es una lista: `|` llega como argumento de echo, no encadena nada."""
    respuesta = await agent.handle(
        Request.new("run_command", argv=["echo", "uno | rm -rf /"])
    )
    assert respuesta.ok
    assert "uno | rm -rf /" in respuesta.result["stdout"]


async def test_run_command_respeta_el_tiempo_limite(caps):
    caps.allowed_executables = ["sleep"]
    caps.command_timeout = 0.2
    respuesta = await NodeAgent(caps).handle(Request.new("run_command", argv=["sleep", "5"]))
    assert not respuesta.ok
    assert "límite" in respuesta.denied_reason


# --- configuración -------------------------------------------------------


def test_falta_el_fichero_lo_dice_claro(tmp_path):
    with pytest.raises(ConfigError, match="No encuentro"):
        load_capabilities(tmp_path / "no-existe.json")


def test_un_node_id_con_comodines_se_rechaza(tmp_path):
    """El id va dentro de un topic: un '+' o un '#' lo haría casar con topics ajenos."""
    fichero = tmp_path / "c.json"
    for malo in ("pc/uno", "pc+", "#"):
        fichero.write_text(json.dumps({"node_id": malo}))
        with pytest.raises(ConfigError):
            load_capabilities(fichero)


def test_write_roots_sin_allow_writes_es_un_error(tmp_path):
    """Casi siempre es un despiste, y arrancaría en un estado que nadie pidió."""
    fichero = tmp_path / "c.json"
    fichero.write_text(json.dumps({"node_id": "pc", "write_roots": ["/tmp"]}))
    with pytest.raises(ConfigError, match="allow_writes"):
        load_capabilities(fichero)


def test_una_configuracion_minima_da_un_nodo_inofensivo(tmp_path):
    fichero = tmp_path / "c.json"
    fichero.write_text(json.dumps({"node_id": "pc-carlos"}))
    caps = load_capabilities(fichero)
    assert caps.allow_writes is False
    assert caps.allowed_executables == []
    assert set(caps.manifest()["operations"]) == {"system_info", "list_directory", "read_file"}

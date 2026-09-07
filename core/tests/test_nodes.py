"""Tests del registro de nodos y de las herramientas que lo exponen.

El registro se prueba entero sin broker: recibe una función de publicación de mentira
y se le entregan los mensajes a mano, que es exactamente para lo que se diseñó
agnóstico del transporte.
"""

import asyncio
import json

import pytest
from jarvis_core.nodes.protocol import topic_request
from jarvis_core.nodes.registry import NodeError, NodeRegistry
from jarvis_core.policy.rules import Decision, PolicyEngine
from jarvis_core.tools.builtin.node_tools import ListNodesTool, NodeOperationTool

MANIFIESTO = {
    "node_id": "pc-carlos",
    "operations": ["system_info", "read_file", "run_command"],
    "read_roots": ["/home/carlos"],
    "write_roots": [],
    "executables": ["git"],
    "allow_writes": False,
}


def _registro_con_nodo(publicaciones=None):
    publicadas = publicaciones if publicaciones is not None else []
    reg = NodeRegistry(lambda t, p: publicadas.append((t, p)), timeout=0.3)
    reg.on_manifest("pc-carlos", json.dumps(MANIFIESTO))
    reg.on_status("pc-carlos", '{"online": true}')
    return reg, publicadas


# --- descubrimiento -----------------------------------------------------


def test_un_nodo_se_conoce_por_su_manifiesto():
    reg, _ = _registro_con_nodo()
    info = reg.get("pc-carlos")
    assert info.operations == ["system_info", "read_file", "run_command"]
    assert info.executables == ["git"]
    assert info.online is True


def test_un_manifiesto_ilegible_no_tumba_el_registro():
    reg = NodeRegistry()
    reg.on_manifest("pc", "{roto")
    assert reg.get("pc") is None


def test_el_testamento_marca_el_nodo_como_caido():
    reg, _ = _registro_con_nodo()
    reg.on_status("pc-carlos", '{"online": false}')
    assert reg.get("pc-carlos").online is False


def test_el_registro_no_inventa_capacidades():
    """Completar lo que el nodo no declaró haría prometer algo que va a rechazar."""
    reg = NodeRegistry()
    reg.on_manifest("pc", json.dumps({"node_id": "pc"}))
    info = reg.get("pc")
    assert info.operations == []
    assert info.allow_writes is False


# --- llamadas ------------------------------------------------------------


async def test_una_llamada_publica_en_el_topic_del_nodo():
    reg, publicadas = _registro_con_nodo()

    async def responder():
        await asyncio.sleep(0.01)
        _, payload = publicadas[0]
        reg.on_response(json.dumps({"id": json.loads(payload)["id"], "ok": True, "result": {"x": 1}}))

    tarea = asyncio.create_task(responder())
    resultado = await reg.call("pc-carlos", "system_info")
    await tarea
    assert publicadas[0][0] == topic_request("pc-carlos")
    assert resultado == {"x": 1}


async def test_un_nodo_desconocido_lo_dice_y_lista_los_que_hay():
    reg, _ = _registro_con_nodo()
    with pytest.raises(NodeError, match="pc-carlos"):
        await reg.call("portatil", "system_info")


async def test_un_nodo_caido_no_recibe_peticiones():
    reg, publicadas = _registro_con_nodo()
    reg.on_status("pc-carlos", '{"online": false}')
    with pytest.raises(NodeError, match="desconectado"):
        await reg.call("pc-carlos", "system_info")
    assert publicadas == []


async def test_una_operacion_fuera_del_manifiesto_no_viaja():
    """Cortesía, no seguridad: el nodo la revalida igual. Pero ahorra el viaje."""
    reg, publicadas = _registro_con_nodo()
    with pytest.raises(NodeError, match="no ofrece"):
        await reg.call("pc-carlos", "write_file", {"path": "/x"})
    assert publicadas == []


async def test_sin_transporte_se_dice_en_vez_de_fallar_raro():
    reg = NodeRegistry()
    reg.on_manifest("pc-carlos", json.dumps(MANIFIESTO))
    reg.on_status("pc-carlos", '{"online": true}')
    with pytest.raises(NodeError, match="transporte"):
        await reg.call("pc-carlos", "system_info")


async def test_el_tiempo_limite_no_deja_peticiones_colgadas():
    """Sin soltar el futuro, la tabla crecería con peticiones que nadie contestará."""
    reg, _ = _registro_con_nodo()
    with pytest.raises(NodeError, match="no respondió"):
        await reg.call("pc-carlos", "system_info")
    assert reg._pending == {}


async def test_dos_peticiones_en_vuelo_no_se_pisan():
    """Sin correlación por id, la primera respuesta se atribuiría a la otra petición."""
    reg, publicadas = _registro_con_nodo()

    primera = asyncio.create_task(reg.call("pc-carlos", "system_info"))
    segunda = asyncio.create_task(reg.call("pc-carlos", "read_file", {"path": "/a"}))
    await asyncio.sleep(0.01)

    ids = [json.loads(p)["id"] for _, p in publicadas]
    # Se contesta en orden inverso, que es el caso que rompe una cola sin ids.
    reg.on_response(json.dumps({"id": ids[1], "ok": True, "result": "de-la-segunda"}))
    reg.on_response(json.dumps({"id": ids[0], "ok": True, "result": "de-la-primera"}))

    assert await primera == "de-la-primera"
    assert await segunda == "de-la-segunda"


async def test_una_respuesta_huerfana_se_descarta():
    reg, _ = _registro_con_nodo()
    reg.on_response(json.dumps({"id": "no-existe", "ok": True, "result": 1}))  # no revienta


async def test_una_denegacion_del_nodo_llega_con_su_motivo():
    """El modelo necesita el motivo para corregir el tiro, no para reintentar igual."""
    reg, publicadas = _registro_con_nodo()

    async def responder():
        await asyncio.sleep(0.01)
        rid = json.loads(publicadas[0][1])["id"]
        reg.on_response(json.dumps({
            "id": rid, "ok": False, "error": "no permitido",
            "denied_reason": "'/etc/passwd' queda fuera de las raíces permitidas.",
        }))

    tarea = asyncio.create_task(responder())
    with pytest.raises(NodeError, match="fuera de las raíces"):
        await reg.call("pc-carlos", "read_file", {"path": "/etc/passwd"})
    await tarea


# --- herramientas --------------------------------------------------------


async def test_list_nodes_sin_nodos_explica_como_añadir_uno():
    resultado = await ListNodesTool(NodeRegistry()).run()
    assert "jarvis-node" in resultado.content


async def test_list_nodes_muestra_lo_que_ofrece_cada_maquina():
    reg, _ = _registro_con_nodo()
    resultado = await ListNodesTool(reg).run()
    datos = json.loads(resultado.content)
    assert datos[0]["node_id"] == "pc-carlos"
    assert "run_command" in datos[0]["operations"]


async def test_node_operation_exige_nodo_y_operacion():
    resultado = await NodeOperationTool(NodeRegistry()).run(node_id="pc")
    assert resultado.is_error


async def test_un_fallo_del_nodo_vuelve_como_error_no_como_excepcion():
    reg, _ = _registro_con_nodo()
    resultado = await NodeOperationTool(reg).run(node_id="fantasma", operation="system_info")
    assert resultado.is_error
    assert "No conozco" in resultado.content


def test_el_alcance_de_la_concesion_es_nodo_mas_operacion():
    """Aprobar leer en un PC no debe abrir escribir, ni leer en otra máquina."""
    tool = NodeOperationTool(NodeRegistry())
    subject = tool.policy_subject({"node_id": "pc-carlos", "operation": "read_file"})
    assert subject["target"] == "pc-carlos:read_file"
    assert tool.policy_scope_key == "target"


def test_una_concesion_no_se_extiende_a_otra_operacion_del_mismo_nodo():
    tool = NodeOperationTool(NodeRegistry())
    engine = PolicyEngine()
    from jarvis_core.policy.rules import Rule

    engine.grant(
        "s1",
        Rule(tool="node_operation", decision=Decision.ALLOW,
             match={"target": "pc-carlos:read_file"}),
    )
    leer = tool.policy_subject({"node_id": "pc-carlos", "operation": "read_file"})
    escribir = tool.policy_subject({"node_id": "pc-carlos", "operation": "write_file"})
    otro_pc = tool.policy_subject({"node_id": "servidor", "operation": "read_file"})

    assert engine.evaluate("node_operation", leer, baseline=Decision.ASK, session="s1").allowed
    assert not engine.evaluate("node_operation", escribir, baseline=Decision.ASK, session="s1").allowed
    assert not engine.evaluate("node_operation", otro_pc, baseline=Decision.ASK, session="s1").allowed


def test_node_operation_pide_confirmacion_por_defecto():
    assert NodeOperationTool(NodeRegistry()).requires_confirmation is True
    assert ListNodesTool(NodeRegistry()).requires_confirmation is False

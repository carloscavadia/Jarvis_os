"""El conector HTTP genérico: cualquier API de tu red, sin herramienta a medida.

Es la pieza que hace que «buscarse la vida» deje de depender de que alguien
anticipe cada integración. Y es la más peligrosa de todas: un cliente HTTP con
tus credenciales dentro. Lo que la hace segura es que **el modelo no elige la
ruta** — solo puede usar las que se declararon al registrar el módulo, y la
separación lectura/escritura sigue decidiendo qué pide aprobación.
"""

import asyncio

import pytest
from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.connectors.store import SUPPORTED_TYPES, ConnectorStore

MAESTRA = "master-key-" * 4


@pytest.fixture()
def runtime(tmp_path):
    store = ConnectorStore(str(tmp_path / "c.db"), MAESTRA)
    store.upsert(
        "proxmox",
        "http",
        {
            "url": "https://192.168.68.201:8006",
            "auth_header": "Authorization",
            "auth_prefix": "PVEAPIToken=",
            "read_actions": ["GET /api2/json/nodes"],
            "write_actions": ["POST /api2/json/nodes/pve/qemu/100/status/start"],
        },
        {"token": "root@pam!jarvis=secreto"},
    )
    entorno = ConnectorRuntime(store, timeout=5, max_payload_bytes=4096, max_response_bytes=65536)
    llamadas = []

    def _request(url, *, method, headers, payload=None):
        from jarvis_core.tools.base import ToolResult

        llamadas.append({"url": url, "method": method, "headers": headers, "payload": payload})
        return ToolResult("{}")

    entorno._request = _request
    entorno.llamadas = llamadas
    return entorno


def test_el_tipo_generico_existe():
    assert "http" in SUPPORTED_TYPES


def test_una_accion_declarada_se_ejecuta(runtime):
    resultado = asyncio.run(
        runtime.invoke("proxmox", "GET /api2/json/nodes", {}, write=False)
    )

    assert not resultado.is_error
    llamada = runtime.llamadas[0]
    assert llamada["url"] == "https://192.168.68.201:8006/api2/json/nodes"
    assert llamada["method"] == "GET"


def test_la_cabecera_de_autenticacion_es_configurable(runtime):
    """No hay dos APIs de acuerdo: Bearer, PVEAPIToken, X-API-Key…"""
    asyncio.run(runtime.invoke("proxmox", "GET /api2/json/nodes", {}, write=False))

    assert runtime.llamadas[0]["headers"]["Authorization"] == "PVEAPIToken=root@pam!jarvis=secreto"


def test_una_ruta_no_declarada_se_rechaza(runtime):
    """Lo único que separa esto de un cliente HTTP arbitrario con tus claves."""
    resultado = asyncio.run(
        runtime.invoke("proxmox", "GET /api2/json/access/users", {}, write=False)
    )

    assert resultado.is_error
    assert "no permitida" in resultado.content.lower()
    assert not runtime.llamadas, "no debería haber salido ninguna petición"


def test_una_escritura_no_puede_colarse_como_lectura(runtime):
    """Es lo que sostiene la aprobación humana: si se pudiera, encender una
    máquina no pediría permiso."""
    resultado = asyncio.run(
        runtime.invoke(
            "proxmox", "POST /api2/json/nodes/pve/qemu/100/status/start", {}, write=False
        )
    )

    assert resultado.is_error
    assert not runtime.llamadas


def test_la_escritura_declarada_si_pasa_por_su_canal(runtime):
    resultado = asyncio.run(
        runtime.invoke(
            "proxmox", "POST /api2/json/nodes/pve/qemu/100/status/start", {}, write=True
        )
    )

    assert not resultado.is_error
    assert runtime.llamadas[0]["method"] == "POST"


def test_los_parametros_se_codifican_en_vez_de_concatenarse(runtime):
    """Vienen del modelo: es la diferencia entre un filtro y una ruta inyectada."""
    asyncio.run(
        runtime.invoke(
            "proxmox", "GET /api2/json/nodes", {"query": {"type": "vm & lxc"}}, write=False
        )
    )

    url = runtime.llamadas[0]["url"]
    assert "type=vm+%26+lxc" in url or "type=vm%20%26%20lxc" in url
    assert " " not in url


def test_una_accion_mal_formada_se_explica(runtime, tmp_path):
    store = ConnectorStore(str(tmp_path / "d.db"), MAESTRA)
    store.upsert("raro", "http", {"url": "https://nas.local", "read_actions": ["nodes"]}, {"token": "x"})
    entorno = ConnectorRuntime(store, timeout=5, max_payload_bytes=4096, max_response_bytes=4096)

    resultado = asyncio.run(entorno.invoke("raro", "nodes", {}, write=False))

    assert resultado.is_error
    assert "GET /ruta" in resultado.content


def test_un_get_no_lleva_cuerpo(runtime):
    asyncio.run(
        runtime.invoke("proxmox", "GET /api2/json/nodes", {"body": {"a": 1}}, write=False)
    )
    assert runtime.llamadas[0]["payload"] is None


def test_un_destino_fuera_de_la_red_se_rechaza(tmp_path):
    """La validación de URL del registro sigue aplicando al genérico."""
    store = ConnectorStore(str(tmp_path / "e.db"), MAESTRA)
    with pytest.raises(ValueError):
        from jarvis_core.tools.builtin.connectors import validate_connector_url

        validate_connector_url("file:///etc/passwd")
    assert store is not None

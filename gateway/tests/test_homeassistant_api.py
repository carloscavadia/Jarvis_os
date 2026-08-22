"""Tests de la matriz domótica que consume el HUD.

Estos dos endpoints existían desde hacía tiempo y **nunca habían funcionado**:
importaban `jarvis_core.connectors.storage`, un módulo inexistente, y leían
`settings.connector_db`, un campo inexistente. Un `except Exception` mudo se
tragaba ambos errores, así que el panel del HUD salía siempre vacío sin ninguna
pista. `test_endpoint_contracts.py` los daba por buenos porque sólo comprueba
que la ruta exista y tenga llave, no lo que hace.

Ahora van por `ConnectorRuntime`, el mismo camino que las herramientas del
agente, y estos tests recorren esa integración de punta a punta.
"""

import json

import pytest
from fastapi.testclient import TestClient
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.tools.base import ToolResult
from jarvis_gateway import app as gateway_module

KEY = {"X-Jarvis-Key": "ci-test-key"}

_ESTADOS = [
    {"entity_id": "light.salon", "state": "on", "attributes": {"friendly_name": "Salón"}},
    {"entity_id": "switch.cafetera", "state": "off", "attributes": {}},
    {
        "entity_id": "sensor.temperatura",
        "state": "21.4",
        "attributes": {"friendly_name": "Temperatura", "unit_of_measurement": "°C"},
    },
    # Fuera de los dominios que la matriz sabe pintar: no debe llegar al HUD.
    {"entity_id": "automation.rutina", "state": "on", "attributes": {}},
]


@pytest.fixture()
def store(monkeypatch, tmp_path):
    store = ConnectorStore(str(tmp_path / "connectors.db"), "master-key-" * 4)
    monkeypatch.setattr(gateway_module.sessions, "connector_store", store)
    yield store
    store.close()


def _registrar_ha(store, *, write_actions=("homeassistant.service",), enabled=True):
    store.upsert(
        "casa",
        "home_assistant",
        {
            "url": "http://home.local:8123",
            "services": ["domotica"],
            "read_actions": ["homeassistant.state"],
            "write_actions": list(write_actions),
        },
        {"token": "token-de-home-assistant"},
        enabled=enabled,
    )


def _fingir_runtime(monkeypatch, resultado, capturado=None):
    async def invoke(self, connector, action, payload, *, write):
        if capturado is not None:
            capturado.append((connector, action, payload, write))
        return resultado

    monkeypatch.setattr(gateway_module.ConnectorRuntime, "invoke", invoke)


def test_las_entidades_llegan_al_hud_con_la_forma_que_pinta_la_matriz(
    store, monkeypatch
):
    _registrar_ha(store)
    _fingir_runtime(
        monkeypatch,
        ToolResult(json.dumps({"total": 4, "entities": [
            {"entity_id": e["entity_id"],
             "name": e["attributes"].get("friendly_name", e["entity_id"]),
             "domain": e["entity_id"].partition(".")[0],
             "state": e["state"],
             **({"unit": e["attributes"]["unit_of_measurement"]}
                if "unit_of_measurement" in e["attributes"] else {})}
            for e in _ESTADOS
        ]})),
    )

    with TestClient(gateway_module.app) as client:
        response = client.get("/homeassistant/entities", headers=KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [e["entity_id"] for e in body["entities"]] == [
        "light.salon",
        "switch.cafetera",
        "sensor.temperatura",
    ]
    salon = body["entities"][0]
    assert salon["name"] == "Salón" and salon["state"] == "on"
    assert body["entities"][2]["unit"] == "°C"


def test_sin_modulo_registrado_el_hud_recibe_un_404_explicativo(store):
    with TestClient(gateway_module.app) as client:
        response = client.get("/homeassistant/entities", headers=KEY)

    assert response.status_code == 404
    assert "CONECTORES" in response.json()["detail"]


def test_un_modulo_desactivado_no_cuenta_como_disponible(store):
    _registrar_ha(store, enabled=False)
    with TestClient(gateway_module.app) as client:
        assert client.get("/homeassistant/entities", headers=KEY).status_code == 404


def test_conmutar_una_entidad_invoca_el_servicio_como_escritura(store, monkeypatch):
    _registrar_ha(store)
    llamadas: list[tuple] = []
    _fingir_runtime(monkeypatch, ToolResult("[]"), llamadas)

    with TestClient(gateway_module.app) as client:
        response = client.post(
            "/homeassistant/toggle", headers=KEY, json={"entity_id": "light.salon"}
        )

    assert response.status_code == 200
    connector, action, payload, write = llamadas[0]
    assert (connector, action) == ("casa", "homeassistant.service")
    assert payload == {
        "domain": "light",
        "service": "toggle",
        "entity_id": "light.salon",
    }
    # La separación lectura/escritura es lo que obliga a declarar
    # `homeassistant.service` antes de que el HUD pueda accionar nada.
    assert write is True


def test_sin_la_accion_de_escritura_declarada_el_toggle_se_rechaza(store):
    _registrar_ha(store, write_actions=())

    with TestClient(gateway_module.app) as client:
        response = client.post(
            "/homeassistant/toggle", headers=KEY, json={"entity_id": "light.salon"}
        )

    assert response.status_code == 502
    assert "no permitida" in response.json()["detail"].lower()


def test_un_entity_id_malformado_se_rechaza_antes_de_salir_a_la_red(store):
    _registrar_ha(store)

    with TestClient(gateway_module.app) as client:
        response = client.post(
            "/homeassistant/toggle", headers=KEY, json={"entity_id": "../../etc/passwd"}
        )

    assert response.status_code == 422


def test_un_fallo_de_home_assistant_se_reporta_en_vez_de_silenciarse(store, monkeypatch):
    _registrar_ha(store)
    _fingir_runtime(
        monkeypatch, ToolResult("Home Assistant respondió HTTP 401.", is_error=True)
    )

    with TestClient(gateway_module.app) as client:
        response = client.get("/homeassistant/entities", headers=KEY)

    assert response.status_code == 502
    assert "401" in response.json()["detail"]


def test_ambos_endpoints_siguen_exigiendo_la_llave(store):
    _registrar_ha(store)
    with TestClient(gateway_module.app) as client:
        assert client.get("/homeassistant/entities").status_code == 401
        assert client.post(
            "/homeassistant/toggle", json={"entity_id": "light.salon"}
        ).status_code == 401

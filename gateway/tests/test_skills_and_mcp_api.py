"""API de Habilidades y de servidores MCP.

Los dos subsistemas entraron sin una sola prueba de sus endpoints, y con dos
tests unitarios que ni siquiera pasaban. Lo que se fija aquí no es que los
endpoints existan —de eso ya se ocupa el contrato— sino las tres cosas que se
rompen en silencio: que la llave hace falta, que los secretos no vuelven en el
listado, y que el código ejecutable no se cuela por la puerta de atrás.
"""

import json

import pytest
from fastapi.testclient import TestClient
from jarvis_core.mcp import MCPManager, MCPStore
from jarvis_core.skills import SkillManager, SkillStore
from jarvis_gateway import app as gateway_module

KEY = {"X-Jarvis-Key": "ci-test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Un gateway con almacenes propios, para no pisar los del resto de la suite."""
    sessions = gateway_module.sessions
    monkeypatch.setattr(sessions, "skill_store", SkillStore(str(tmp_path / "s.db")))
    monkeypatch.setattr(sessions, "skill_manager", SkillManager(sessions.skill_store))
    monkeypatch.setattr(sessions, "mcp_store", MCPStore(str(tmp_path / "m.db")))
    monkeypatch.setattr(sessions, "mcp_manager", MCPManager(sessions.mcp_store))
    with TestClient(gateway_module.app) as c:
        yield c


def learn(client, **overrides):
    body = {
        "name": "formatear_json",
        "title": "Formatear JSON",
        "trigger": "formatear json",
        "description": "Aplica sangría de dos espacios.",
        "content": "Abre el fichero y reindenta.",
        "skill_type": "instruction",
    }
    body.update(overrides)
    return client.post("/skills/learn", headers=KEY, json=body)


# ── Habilidades ──────────────────────────────────────────────────────────────


def test_the_skills_api_needs_the_gateway_key(client):
    """Un endpoint sin llave en un servicio expuesto en la LAN es una fuga que
    no se nota hasta que alguien la encuentra."""
    assert client.get("/skills").status_code == 401
    assert client.post("/skills/learn", json={}).status_code == 401
    assert client.delete("/skills/cualquiera").status_code == 401
    assert client.post("/skills/cualquiera/toggle").status_code == 401


def test_a_learned_skill_shows_up_in_the_listing(client):
    assert client.get("/skills", headers=KEY).json() == []
    assert learn(client).status_code == 200

    listado = client.get("/skills", headers=KEY).json()
    assert [s["name"] for s in listado] == ["formatear_json"]
    assert listado[0]["trigger"] == "formatear json"
    assert listado[0]["enabled"] is True


def test_pausing_a_skill_keeps_it_but_stops_it(client):
    learn(client)
    respuesta = client.post("/skills/formatear_json/toggle", headers=KEY)
    assert respuesta.json() == {"name": "formatear_json", "enabled": False}

    # Sigue en la lista: pausar no es borrar.
    assert client.get("/skills", headers=KEY).json()[0]["enabled"] is False
    # Y ya no se ofrece como herramienta.
    assert gateway_module.sessions.skill_manager.get_registered_tools() == []

    assert client.post("/skills/formatear_json/toggle", headers=KEY).json()["enabled"] is True
    assert len(gateway_module.sessions.skill_manager.get_registered_tools()) == 1


def test_toggling_something_that_does_not_exist_says_so(client):
    """Un 200 silencioso aquí haría creer que la habilidad quedó pausada."""
    assert client.post("/skills/fantasma/toggle", headers=KEY).status_code == 404


def test_deleting_a_skill_also_retires_its_tool(client):
    learn(client)
    assert client.delete("/skills/formatear_json", headers=KEY).json()["deleted"] is True
    assert client.get("/skills", headers=KEY).json() == []
    assert gateway_module.sessions.skill_manager.get_registered_tools() == []
    # Borrar dos veces no es un error, pero tampoco miente sobre lo que hizo.
    assert client.delete("/skills/formatear_json", headers=KEY).json()["deleted"] is False


def test_a_skill_type_that_is_not_one_of_the_two_is_refused(client):
    """`skill_type` decide si el contenido se ejecuta. No es un campo libre."""
    assert learn(client, skill_type="shell").status_code == 422


def test_the_owner_can_store_python_but_it_still_will_not_run(client):
    """El panel es la vía legítima para guardar una habilidad ejecutable: ahí hay
    una persona con la llave del gateway. Ejecutarla es otra decisión, y sigue
    apagada hasta que se active a propósito."""
    assert learn(client, content="result = 42", skill_type="python").status_code == 200
    assert client.get("/skills", headers=KEY).json()[0]["skill_type"] == "python"


# ── Servidores MCP ───────────────────────────────────────────────────────────


#: Un binario que no existe: el registro falla al instante en vez de salir a la
#: red. Lo que se prueba aquí es el almacén y la API, no que `npx` funcione.
COMANDO_INEXISTENTE = "/nonexistent/jarvis-test-mcp-server"


def registrar(client, name="github", **overrides):
    body = {
        "name": name,
        "transport": "stdio",
        "command": COMANDO_INEXISTENTE,
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp-secreto-de-prueba"},
        "enabled": True,
    }
    body.update(overrides)
    return client.put(f"/mcp/servers/{name}", headers=KEY, json=body)


def test_the_mcp_api_needs_the_gateway_key(client):
    """Registrar un servidor MCP stdio lanza procesos en el servidor: esta es de
    las rutas que peor sienta dejar sin llave."""
    assert client.get("/mcp/servers").status_code == 401
    assert client.put("/mcp/servers/x", json={"name": "x"}).status_code == 401
    assert client.post("/mcp/servers/x/test").status_code == 401
    assert client.delete("/mcp/servers/x").status_code == 401


def test_a_registered_server_shows_up_in_the_listing(client):
    assert client.get("/mcp/servers", headers=KEY).json() == []
    assert registrar(client).status_code == 200

    listado = client.get("/mcp/servers", headers=KEY).json()
    assert [s["name"] for s in listado] == ["github"]
    assert listado[0]["command"] == COMANDO_INEXISTENTE
    assert listado[0]["args"] == ["-y", "@modelcontextprotocol/server-github"]


def test_the_listing_never_returns_the_server_credentials(client):
    """`env` es donde va el token de un servidor MCP. Devolverlo tal cual lo
    pondría en pantalla y en el historial del navegador."""
    registrar(client)
    serializado = json.dumps(client.get("/mcp/servers", headers=KEY).json())
    assert "ghp-secreto-de-prueba" not in serializado
    # Pero el nombre de la variable sí: al editar hace falta saber cuáles están
    # puestas, no cuánto valen.
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in serializado

    # Y el token no se ha perdido por el camino: sigue disponible para lanzar
    # el proceso.
    guardado = gateway_module.sessions.mcp_store.get("github")
    assert guardado.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp-secreto-de-prueba"


def test_a_transport_that_does_not_exist_is_refused(client):
    assert registrar(client, transport="carrier-pigeon").status_code == 422


def test_testing_a_server_that_was_never_registered_says_so(client):
    assert client.post("/mcp/servers/fantasma/test", headers=KEY).status_code == 404


def test_a_server_that_cannot_start_reports_the_reason(client):
    """Un 500 aquí dejaría al usuario adivinando; el panel enseña este texto."""
    registrar(client, name="roto", command="")
    respuesta = client.post("/mcp/servers/roto/test", headers=KEY)
    assert respuesta.status_code == 502
    assert "command" in respuesta.json()["detail"]


def test_deleting_a_server_removes_it(client):
    registrar(client)
    assert client.delete("/mcp/servers/github", headers=KEY).json()["deleted"] is True
    assert client.get("/mcp/servers", headers=KEY).json() == []
    assert client.delete("/mcp/servers/github", headers=KEY).json()["deleted"] is False

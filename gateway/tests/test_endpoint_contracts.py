"""Contrato de la superficie HTTP del gateway.

Estos tests no comprueban qué hace cada endpoint, sino dos cosas que ninguna
prueba de dominio cubre y que se rompen justo cuando se mueve código:

**Que las rutas siguen existiendo.** Al extraer los endpoints de música a un
router propio quedaron sin registrar —el router las tenía, el `include_router`
estaba donde debía, y aun así `app.routes` no las contenía—. La suite entera dio
verde porque ninguna prueba las recorría. Un inventario explícito convierte esa
clase de fallo en un test rojo en vez de en una llamada del usuario.

**Que ninguna se queda sin llave.** El gateway es un servicio expuesto en la LAN;
un endpoint nuevo que olvide su dependencia de autenticación es una fuga que no
se nota hasta que alguien la encuentra.
"""

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from jarvis_gateway import app as gateway_module

#: Deliberadamente públicas. `/health` y `/ready` las consulta el healthcheck de
#: Docker antes de que exista ninguna sesión, y `/hud` sirve el propio HUD, que
#: sin él no podría ni pedir la clave.
PUBLIC = {"/health", "/ready", "/hud"}

#: Autentican por `?token=` en vez de por cabecera: el navegador no puede poner
#: cabeceras en `<audio src>` ni en `<img src>`.
TOKEN_IN_QUERY = {
    "/music/stream/{song_id}",
    "/music/cover/{cover_id}",
    "/workspace/file/raw",
}

#: Tienen su propia credencial, la de n8n, no la del gateway.
CONNECTOR_KEY = {"/connectors/chat", "/connectors/events"}

EXPECTED_ROUTES = {
    "/agents/swarm", "/chat",
    "/connector-modules", "/connector-modules/test", "/connector-modules/{name}",
    "/connectors/chat", "/connectors/events",
    "/goals/current", "/goals/{goal_id}/control",
    "/health", "/hud", "/ready",
    "/homeassistant/entities", "/homeassistant/toggle",
    "/memory/graph",
    "/music/cover/{cover_id}", "/music/playlist/{playlist_id}", "/music/playlists",
    "/music/random", "/music/search", "/music/status", "/music/stream/{song_id}",
    "/proactive/events", "/proactive/events/{event_id}/decision",
    "/proxmox/status",
    "/server/heal", "/server/health",
    "/tasks", "/tasks/history", "/tasks/{task_id}", "/tasks/{task_id}/control",
    "/voice/realtime-token", "/voice/status", "/voice/synthesize",
    "/voice/transcribe",
    "/web/navigate",
    "/workspace/file/content", "/workspace/file/create", "/workspace/file/delete",
    "/workspace/file/raw", "/workspace/tree",
}

EXPECTED_WEBSOCKETS = {
    "/ws/{session_id}", "/ws/wake/{device_id}", "/ws/voice/{session_id}",
}


def http_routes():
    return [r for r in gateway_module.app.routes if isinstance(r, APIRoute)]


def test_every_expected_route_is_registered():
    """Si una extracción deja rutas fuera, esto lo dice antes que el usuario."""
    registered = {route.path for route in http_routes()}
    missing = EXPECTED_ROUTES - registered
    assert not missing, f"rutas que han desaparecido: {sorted(missing)}"


def test_new_routes_are_declared_here_on_purpose():
    """Obliga a pasar por este archivo al añadir un endpoint.

    No es burocracia: el sitio donde se declara la ruta es el mismo donde se
    decide si lleva llave, y esa decisión es la que no puede olvidarse.
    """
    registered = {route.path for route in http_routes()}
    undeclared = registered - EXPECTED_ROUTES
    assert not undeclared, (
        f"rutas nuevas sin declarar en EXPECTED_ROUTES: {sorted(undeclared)}. "
        "Añádelas y decide si son públicas, de clave del gateway o de conector."
    )


def test_the_three_websockets_are_registered():
    registered = {
        r.path for r in gateway_module.app.routes if isinstance(r, APIWebSocketRoute)
    }
    assert EXPECTED_WEBSOCKETS <= registered


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(r, id=f"{sorted(r.methods - {'HEAD', 'OPTIONS'})[0]} {r.path}")
        for r in http_routes()
        if r.path not in PUBLIC | TOKEN_IN_QUERY | CONNECTOR_KEY
    ],
)
def test_every_private_endpoint_rejects_a_request_without_the_key(route):
    """Un endpoint sin llave es una fuga que no se nota hasta que la encuentran."""
    method = sorted(route.methods - {"HEAD", "OPTIONS"})[0]
    # Los parámetros de ruta se rellenan con algo inofensivo: lo que importa es
    # que la autenticación corte antes de llegar a mirarlos.
    path = route.path.replace("{task_id}", "1").replace("{goal_id}", "1")
    path = path.replace("{event_id}", "1").replace("{name}", "x")
    path = path.replace("{song_id}", "x").replace("{cover_id}", "x")
    path = path.replace("{playlist_id}", "x").replace("{session_id}", "x")

    with TestClient(gateway_module.app) as client:
        response = client.request(method, path, json={})

    assert response.status_code == 401, (
        f"{method} {route.path} respondió {response.status_code} sin clave; "
        "le falta Depends(require_api_key)."
    )


@pytest.mark.parametrize("path", sorted(TOKEN_IN_QUERY))
def test_the_media_endpoints_check_the_token_in_the_query(path):
    """Sin cabeceras posibles, la llave viaja por query; pero sigue siendo obligatoria."""
    concrete = path.replace("{song_id}", "x").replace("{cover_id}", "x")
    with TestClient(gateway_module.app) as client:
        assert client.get(concrete).status_code == 401
        assert client.get(concrete, params={"token": "equivocada"}).status_code == 401


@pytest.mark.parametrize("path", sorted(CONNECTOR_KEY))
def test_the_connector_ingress_does_not_accept_the_gateway_key(path):
    """n8n tiene su propia credencial: la del gateway no debe abrirle la puerta.

    Son caminos de entrada desde fuera, así que confundir las dos llaves
    convertiría la clave del HUD en una llave de ingreso remoto.
    """
    with TestClient(gateway_module.app) as client:
        assert client.post(path, json={}).status_code == 401
        assert client.post(
            path, headers={"X-Jarvis-Key": "ci-test-key"}, json={}
        ).status_code == 401


def test_the_public_endpoints_answer_without_a_key():
    """El healthcheck de Docker los llama antes de que exista ninguna sesión."""
    with TestClient(gateway_module.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code in {200, 503}

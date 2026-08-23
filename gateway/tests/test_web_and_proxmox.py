"""Tests de los dos endpoints de red que el HUD consulta directamente.

Ambos se escribieron a mano en lugar de reutilizar las piezas que el proyecto ya
tenía, y cada uno perdió por el camino una garantía distinta: `/web/navigate` la
validación del destino y del certificado, `/proxmox/status` la honestidad de los
datos y el no bloquear el bucle de eventos.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from jarvis_gateway import app as gateway_module

KEY = {"X-Jarvis-Key": "ci-test-key"}

#: Destinos que un modelo puede pedir y que jamás deben salir del gateway:
#: el router de casa, el resto de la LAN, el propio contenedor y el servicio de
#: metadatos de las nubes, que entrega credenciales a quien lo consulte.
DESTINOS_PROHIBIDOS = [
    "http://192.168.68.201:8006",
    "https://10.0.0.5/admin",
    "http://127.0.0.1:8080/health",
    "http://169.254.169.254/latest/meta-data/",
    "https://homeassistant.local:8123",
    "http://[::1]:8080",
]


@pytest.mark.parametrize("url", DESTINOS_PROHIBIDOS)
def test_web_navigate_rechaza_los_destinos_internos(url):
    with TestClient(gateway_module.app) as client:
        response = client.post("/web/navigate", headers=KEY, json={"url": url})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False, f"{url} no debería ser alcanzable"
    assert "error" in body


def test_web_navigate_extrae_titulo_y_texto_de_una_pagina_publica(monkeypatch):
    html = b"""
        <html><head><title>  Noticias del dia  </title>
        <style>.x{color:red}</style></head>
        <body><script>alert(1)</script><p>Primer parrafo.</p>
        <p>Segundo parrafo.</p></body></html>
    """

    def fake_get(self, url, **kwargs):
        # El cuarto valor dice si hubo que recortar la página. Antes pasarse del
        # tope era un error y no se leía nada; ahora se lee el principio y se
        # avisa, que es lo que arregló la vista previa de páginas grandes.
        return "https://ejemplo.com/final", "text/html", html, False

    monkeypatch.setattr(gateway_module.WebClient, "get", fake_get)

    with TestClient(gateway_module.app) as client:
        response = client.post(
            "/web/navigate", headers=KEY, json={"url": "ejemplo.com"}
        )

    data = response.json()["data"]
    assert data["title"] == "Noticias del dia"
    # El destino final tras redirecciones, no el que se pidió.
    assert data["url"] == "https://ejemplo.com/final"
    assert "Primer parrafo. Segundo parrafo." in data["preview_text"]
    # Ni el script ni el estilo deben acabar en el resumen que ve el usuario.
    assert "alert" not in data["preview_text"]
    assert "color:red" not in data["preview_text"]


def test_web_navigate_usa_el_cliente_con_tls_verificado(monkeypatch):
    """La copia a mano ponía `CERT_NONE`; `WebClient` valida contra certifi."""
    usado: list[str] = []

    def fake_get(self, url, **kwargs):
        usado.append(url)
        return url, "text/html", b"<title>ok</title>"

    monkeypatch.setattr(gateway_module.WebClient, "get", fake_get)

    with TestClient(gateway_module.app) as client:
        client.post("/web/navigate", headers=KEY, json={"url": "ejemplo.com"})

    # Sin esquema explícito se asume HTTPS, nunca HTTP en claro.
    assert usado == ["https://ejemplo.com"]


def test_proxmox_agrega_la_telemetria_real_de_los_nodos(monkeypatch):
    respuesta = {
        "data": [
            {
                "status": "online",
                "cpu": 0.25,
                "mem": 8 * 1024**3,
                "maxmem": 32 * 1024**3,
                "disk": 100 * 1024**3,
                "maxdisk": 500 * 1024**3,
            },
            {"status": "offline", "cpu": 0.9, "mem": 1, "maxmem": 1},
        ]
    }
    monkeypatch.setattr(
        gateway_module.asyncio,
        "to_thread",
        lambda fn, *a, **k: asyncio.sleep(0, result=respuesta),
    )

    with TestClient(gateway_module.app) as client:
        body = client.get("/proxmox/status", headers=KEY).json()

    assert body["online"] is True and body["telemetry"] is True
    assert body["nodes_count"] == 2 and body["online_nodes"] == 1
    # El nodo apagado no entra en la media: 0.25 de un solo nodo online.
    assert body["cpu_pct"] == 25.0
    assert body["ram_pct"] == 25.0
    assert body["disk_pct"] == 20.0


def test_proxmox_inalcanzable_no_inventa_telemetria(monkeypatch):
    """Antes devolvía 12,4 % de CPU y 34,8 % de RAM en cuanto el TCP abría."""

    async def fake_to_thread(fn, *args, **kwargs):
        # La consulta a la API falla; la sonda TCP dice que el host sí contesta.
        if fn.__name__ == "query_api":
            raise OSError("conexión rechazada")
        return True

    monkeypatch.setattr(gateway_module.asyncio, "to_thread", fake_to_thread)

    with TestClient(gateway_module.app) as client:
        body = client.get("/proxmox/status", headers=KEY).json()

    assert body["online"] is True
    assert body["telemetry"] is False
    assert body["cpu_pct"] == 0.0
    assert body["ram_pct"] == 0.0
    assert body["disk_pct"] == 0.0
    assert body["ram_total_bytes"] == 0
    assert "JARVIS_PROXMOX_TOKEN_ID" in body["detail"]


def test_proxmox_host_caido_lo_dice_sin_rodeos(monkeypatch):
    async def fake_to_thread(fn, *args, **kwargs):
        if fn.__name__ == "query_api":
            raise OSError("sin ruta al host")
        return False

    monkeypatch.setattr(gateway_module.asyncio, "to_thread", fake_to_thread)

    with TestClient(gateway_module.app) as client:
        body = client.get("/proxmox/status", headers=KEY).json()

    assert body["online"] is False and body["telemetry"] is False
    assert "No se pudo contactar" in body["detail"]


def test_ambos_endpoints_siguen_exigiendo_la_llave():
    with TestClient(gateway_module.app) as client:
        assert client.get("/proxmox/status").status_code == 401
        assert client.post("/web/navigate", json={"url": "x"}).status_code == 401

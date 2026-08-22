"""Reglas de la casa en caliente.

El prompt de sistema se congela al construir cada orquestador, así que cambiar
el tono o las reglas exigía reiniciar el gateway. Ahora viaja por la capa
volátil, que además es la que no invalida el prefijo cacheado del modelo.
"""

from fastapi.testclient import TestClient
from jarvis_gateway import app as gateway_module

KEY = {"X-Jarvis-Key": "ci-test-key"}


def test_por_defecto_devuelve_lo_que_venga_del_entorno():
    with TestClient(gateway_module.app) as client:
        cuerpo = client.get("/persona", headers=KEY).json()
    assert "overlay" in cuerpo and cuerpo["name"]


def test_se_aplica_y_se_lee_de_vuelta():
    with TestClient(gateway_module.app) as client:
        respuesta = client.put(
            "/persona", headers=KEY, json={"overlay": "Trátame de usted. Sin emojis."}
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["applied"] is True
        assert client.get("/persona", headers=KEY).json()["overlay"] == (
            "Trátame de usted. Sin emojis."
        )


def test_alcanza_a_las_sesiones_ya_abiertas(monkeypatch):
    """Lo que importa: no vale con que lo hereden las sesiones nuevas."""
    with TestClient(gateway_module.app) as client:
        orquestador = gateway_module.sessions._sessions.setdefault(
            "sesion-viva", _OrquestadorFalso()
        )
        client.put("/persona", headers=KEY, json={"overlay": "Habla en gallego."})
        assert orquestador.persona_aplicada == "Habla en gallego."
    gateway_module.sessions._sessions.pop("sesion-viva", None)


def test_se_puede_vaciar():
    with TestClient(gateway_module.app) as client:
        client.put("/persona", headers=KEY, json={"overlay": "Algo"})
        client.put("/persona", headers=KEY, json={"overlay": ""})
        assert client.get("/persona", headers=KEY).json()["overlay"] == ""


def test_exige_la_llave():
    with TestClient(gateway_module.app) as client:
        assert client.get("/persona").status_code == 401
        assert client.put("/persona", json={"overlay": "x"}).status_code == 401


class _OrquestadorFalso:
    def __init__(self) -> None:
        self.persona_aplicada = ""

    def set_persona(self, overlay: str) -> None:
        self.persona_aplicada = overlay

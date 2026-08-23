"""Una página grande se recorta; no se convierte en un error.

El caso real: abrir un enlace de YouTube en el visor web devolvía «La página
supera el límite de descarga» y no se leía nada. El tope existe para acotar la
memoria, y recortar la acota igual —se leen los mismos bytes y se para ahí—,
así que rechazar no protegía de nada: solo convertía cualquier página grande en
una herramienta que no funciona.
"""

import asyncio
import io

import pytest
from jarvis_core.tools.builtin.web_tools import FetchWebPageTool, WebClient


class FakeHeaders:
    def __init__(self, tipo):
        self.tipo = tipo

    def get_content_type(self):
        return self.tipo


class FakeResponse(io.BytesIO):
    def __init__(self, data, tipo="text/html"):
        super().__init__(data)
        self.headers = FakeHeaders(tipo)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture()
def cliente(monkeypatch):
    c = WebClient(timeout=5, max_bytes=1000)
    monkeypatch.setattr(
        "jarvis_core.tools.builtin.web_tools.validate_public_https_url", lambda u: u
    )
    return c


def test_una_pagina_enorme_se_lee_por_arriba_en_vez_de_fallar(cliente, monkeypatch):
    gigante = b"<html><head><title>Portada</title></head><body>" + b"x" * 500000
    monkeypatch.setattr(cliente._opener, "open", lambda *a, **k: FakeResponse(gigante))

    url, tipo, cuerpo, recortada = cliente.get("https://www.youtube.com/watch?v=abc")

    assert recortada is True
    assert len(cuerpo) == 1000
    # Y lo que se lee es el principio, que es donde está el título.
    assert b"<title>Portada</title>" in cuerpo


def test_una_pagina_normal_no_se_marca_como_recortada(cliente, monkeypatch):
    monkeypatch.setattr(
        cliente._opener, "open", lambda *a, **k: FakeResponse(b"<html><body>corta</body></html>")
    )
    _, _, cuerpo, recortada = cliente.get("https://ejemplo.com/")
    assert recortada is False
    assert b"corta" in cuerpo


def test_la_herramienta_avisa_de_que_solo_leyo_el_principio(cliente, monkeypatch):
    """Sin el aviso, el modelo da por completa una página que leyó a medias.

    Y entonces responde que algo «no aparece» cuando lo que pasa es que no llegó
    a esa parte, que es peor que decir que no pudo leerla.
    """
    gigante = b"<html><body>" + b"palabra " * 200000
    monkeypatch.setattr(cliente._opener, "open", lambda *a, **k: FakeResponse(gigante))

    salida = asyncio.run(FetchWebPageTool(cliente).run(url="https://larga.example/"))

    assert not salida.is_error
    assert "solo el principio" in salida.content


def test_una_pagina_corta_no_lleva_aviso(cliente, monkeypatch):
    monkeypatch.setattr(
        cliente._opener,
        "open",
        lambda *a, **k: FakeResponse(b"<html><body>todo cabe</body></html>"),
    )
    salida = asyncio.run(FetchWebPageTool(cliente).run(url="https://corta.example/"))
    assert "solo el principio" not in salida.content
    assert "todo cabe" in salida.content

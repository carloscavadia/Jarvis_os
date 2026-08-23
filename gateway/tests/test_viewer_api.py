"""Lo que el gateway le dice al HUD cuando JARVIS abre algo para mirar.

La orden sale de los *argumentos* de la herramienta, no de su resultado: el
archivo nunca entra en la conversación. Y sale ya validada, porque lo que el
HUD recibe acaba dentro de un `<img>` o de un `<iframe>`.
"""

import json

from fastapi.testclient import TestClient
from jarvis_gateway import app as gateway_module
from jarvis_gateway import runtime
from jarvis_gateway.app import _viewer_presentation

KEY = {"X-Jarvis-Key": "ci-test-key"}


def test_otra_herramienta_no_abre_ventanas():
    assert _viewer_presentation("create_file", {"kind": "image", "path": "x.png"}) is None


def test_una_imagen_baja_con_su_ruta():
    vista = _viewer_presentation(
        "open_viewer", {"kind": "image", "path": "fotos/casa.png", "title": "La casa"}
    )
    assert vista == {"kind": "image", "title": "La casa", "path": "fotos/casa.png"}


def test_un_video_baja_como_identificador_y_no_como_url():
    """El HUD compone la dirección del iframe; aquí solo viaja el identificador.

    Si bajara la URL entera, cualquier cadena que el modelo llamase «de YouTube»
    llegaría intacta al iframe. Con el identificador de 11 caracteres no hay
    forma de colar un destino distinto.
    """
    vista = _viewer_presentation(
        "open_viewer",
        {"kind": "video", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert vista["embed"] == "dQw4w9WgXcQ"
    assert "url" not in vista


def test_un_video_que_no_lo_es_no_baja_nada():
    assert _viewer_presentation("open_viewer", {"kind": "video", "url": "https://mal.example/x"}) is None


def test_una_web_privada_no_baja_al_hud():
    assert _viewer_presentation("open_viewer", {"kind": "web", "url": "https://10.0.0.1/"}) is None


def test_un_tipo_inventado_no_baja_nada():
    assert _viewer_presentation("open_viewer", {"kind": "iframe", "url": "https://x.example"}) is None


def test_sin_ruta_no_hay_ventana():
    assert _viewer_presentation("open_viewer", {"kind": "image", "title": "vacío"}) is None


def test_el_pdf_se_sirve_para_verlo_y_no_para_descargarlo(tmp_path, monkeypatch):
    """En una ventana, `Content-Disposition: attachment` no muestra nada.

    Va en línea, pero con la misma CSP que aísla el resto: `sandbox` lo deja en
    un origen opaco, así que el visor del navegador lo pinta y lo que haya
    dentro no alcanza el almacenamiento del HUD —que es donde vive la llave—.
    """
    raiz = tmp_path / "ws"
    raiz.mkdir()
    (raiz / "informe.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    monkeypatch.setattr(runtime.settings, "workspace_root", str(raiz))

    with TestClient(gateway_module.app) as client:
        respuesta = client.get("/workspace/file/raw?path=informe.pdf&token=ci-test-key")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("application/pdf")
    assert "content-disposition" not in respuesta.headers
    assert "sandbox" in respuesta.headers["content-security-policy"]
    assert respuesta.headers["x-content-type-options"] == "nosniff"


def test_un_html_del_workspace_sigue_bajando_como_descarga(tmp_path, monkeypatch):
    """Lo que cambió es el PDF, no la regla.

    Un `.html` servido con su propio tipo en el origen del HUD podría leer su
    `localStorage`. Basta con que JARVIS escriba uno en el workspace.
    """
    raiz = tmp_path / "ws"
    raiz.mkdir()
    (raiz / "trampa.html").write_text("<script>robar()</script>", encoding="utf-8")
    monkeypatch.setattr(runtime.settings, "workspace_root", str(raiz))

    with TestClient(gateway_module.app) as client:
        respuesta = client.get("/workspace/file/raw?path=trampa.html&token=ci-test-key")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in respuesta.headers["content-disposition"]


def test_sin_llave_no_se_sirve_nada(tmp_path, monkeypatch):
    raiz = tmp_path / "ws"
    raiz.mkdir()
    (raiz / "informe.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(runtime.settings, "workspace_root", str(raiz))
    with TestClient(gateway_module.app) as client:
        assert client.get("/workspace/file/raw?path=informe.pdf&token=no").status_code == 401


# ── Navegador ────────────────────────────────────────────────────────────────

def test_el_paso_del_navegador_no_manda_la_captura_por_el_websocket():
    """Baja un número de versión, no el PNG.

    Un PNG en base64 son cientos de kilobytes por paso, en el mismo canal que
    lleva el texto que se está hablando. El HUD pide la imagen aparte.
    """
    from jarvis_core.tools.base import ToolResult
    from jarvis_gateway.app import _browser_view

    resultado = ToolResult(content=json.dumps({
        "url": "https://cocina.example/paella",
        "title": "La paella",
        "text": "Arroz y azafrán",
        "links": [{"index": 1, "text": "Siguiente", "url": "https://cocina.example/2"}],
        "screenshot_version": 4,
    }))
    vista = _browser_view("browse", resultado)
    assert vista["version"] == 4
    assert vista["url"] == "https://cocina.example/paella"
    assert "text" not in vista


def test_un_paso_fallido_no_abre_ventana():
    from jarvis_core.tools.base import ToolResult
    from jarvis_gateway.app import _browser_view

    assert _browser_view("browse", ToolResult(content="no se pudo", is_error=True)) is None
    assert _browser_view("fetch_web_page", ToolResult(content='{"url":"x"}')) is None


def test_la_captura_pide_llave():
    with TestClient(gateway_module.app) as client:
        assert client.get("/browser/screenshot?token=no-es").status_code == 401


def test_sin_captura_todavia_no_inventa_una():
    from jarvis_core import browser as browser_module

    browser_module.reset_browser_session()
    with TestClient(gateway_module.app) as client:
        assert client.get("/browser/screenshot?token=ci-test-key").status_code == 404


def test_la_captura_no_se_cachea(monkeypatch):
    """Si el navegador la sirviera desde caché, la ventana se quedaría congelada."""
    from jarvis_core import browser as browser_module

    browser_module.reset_browser_session()
    sesion = browser_module.get_browser_session(runtime.settings)
    sesion.last_screenshot = b"\x89PNG-de-mentira"
    try:
        with TestClient(gateway_module.app) as client:
            respuesta = client.get("/browser/screenshot?token=ci-test-key&v=3")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "image/png"
        assert respuesta.headers["cache-control"] == "no-store"
    finally:
        browser_module.reset_browser_session()


def test_con_el_navegador_apagado_no_se_puede_operar(monkeypatch):
    monkeypatch.setattr(runtime.settings, "browser_enabled", False)
    with TestClient(gateway_module.app) as client:
        respuesta = client.post("/browser/act", headers=KEY, json={"action": "click", "x": 1, "y": 1})
    assert respuesta.status_code == 403


def test_operar_el_navegador_pide_llave():
    with TestClient(gateway_module.app) as client:
        assert client.post("/browser/act", json={"action": "back"}).status_code == 401

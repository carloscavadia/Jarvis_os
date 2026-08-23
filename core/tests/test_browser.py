"""El navegador que JARVIS conduce.

Es la herramienta más peligrosa del sistema: un Chromium entero corriendo dentro
de la red de casa. Lo que se fija aquí es dónde NO puede llegar, y que el
encadenado de pasos —buscar, pulsar el tercero, leer— funciona de verdad.
"""

import asyncio
import json

import pytest
from jarvis_core.browser import BrowserUnavailable, is_blocked_subresource
from jarvis_core.browser.session import BrowserSession
from jarvis_core.tools.builtin.browser_tool import BrowseTool


# ── Dobles del navegador ──────────────────────────────────────────────────────
# Playwright no está instalado en CI a propósito: Chromium pesa cientos de megas.
# Estos dobles imitan lo justo de su API, así que si esa API cambia el fallo será
# en el servidor y no aquí. A cambio, todo lo que sí se prueba —las decisiones
# nuestras— corre en cada commit.

class FakePage:
    def __init__(self, paginas):
        self.paginas = paginas
        self.url = "about:blank"
        self.historial = []
        self.rutas = []
        self.clics = []
        self.rueda = []
        self.rellenos = []
        self.capturas = 0
        self.mouse = self
        self.keyboard = self

    async def goto(self, url, **kwargs):
        self.historial.append(url)
        # Una página puede redirigir a otra parte; es el caso que importa.
        self.url = self.paginas.get(url, {}).get("redirects_to", url)

    async def go_back(self, **kwargs):
        if len(self.historial) > 1:
            self.historial.pop()
            self.url = self.historial[-1]

    async def title(self):
        return self.paginas.get(self.url, {}).get("title", "")

    async def inner_text(self, selector):
        return self.paginas.get(self.url, {}).get("text", "")

    async def eval_on_selector_all(self, selector, script):
        return self.paginas.get(self.url, {}).get("links", [])

    async def screenshot(self, **kwargs):
        self.capturas += 1
        return b"PNG" + str(self.capturas).encode()

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def fill(self, selector, text):
        self.rellenos.append((selector, text))

    async def press(self, key):
        self.clics.append(("key", key))

    async def click(self, x, y):
        self.clics.append((x, y))

    async def wheel(self, dx, dy):
        self.rueda.append(dy)


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.rutas = []

    def set_default_timeout(self, ms):
        self.timeout = ms

    async def route(self, patron, handler):
        self.rutas.append((patron, handler))

    async def new_page(self):
        return self.page


class FakeBrowser:
    def __init__(self, page):
        self.context = FakeContext(page)
        self.opciones = None

    async def new_context(self, **kwargs):
        self.opciones = kwargs
        return self.context

    async def close(self):
        self.cerrado = True


class FakeLauncher:
    def __init__(self, paginas):
        self.page = FakePage(paginas)
        self.browser = FakeBrowser(self.page)

    async def launch(self, **kwargs):
        return self.browser


PAGINAS = {
    "https://duckduckgo.com/?q=recetas+de+paella": {
        "title": "recetas de paella",
        "text": "Resultados",
        "links": [
            {"text": "La paella de verdad", "url": "https://cocina.example/paella"},
            {"text": "Otra receta", "url": "https://otra.example/paella"},
        ],
    },
    "https://cocina.example/paella": {
        "title": "La paella de verdad",
        "text": "Arroz, azafrán y paciencia.",
        "links": [],
    },
}


@pytest.fixture()
def sesion(monkeypatch):
    lanzador = FakeLauncher(dict(PAGINAS))
    s = BrowserSession(timeout_seconds=5, launcher=lanzador)
    # La validación pública resuelve DNS de verdad; en el test se sustituye por
    # una regla equivalente que no toca la red.
    def validar(url):
        import urllib.parse
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https":
            raise ValueError("Solo se permiten URLs HTTPS públicas.")
        if (p.hostname or "").startswith(("192.168.", "10.", "127.")) or p.hostname == "localhost":
            raise ValueError("El destino resuelve a una red privada o reservada.")
        return url

    monkeypatch.setattr("jarvis_core.browser.session.validate_public_https_url", validar)
    return s, lanzador


# ── Lo que no puede cargar la página ─────────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://192.168.68.1/admin",
        "https://192.168.68.1/admin",
        "https://127.0.0.1:8080/",
        "https://localhost/",
        "https://router.local/",
        "https://nas.lan/",
        "https://169.254.169.254/latest/meta-data/",
        "chrome://settings",
    ],
)
def test_los_subrecursos_hacia_dentro_de_la_red_se_abortan(url):
    """Una página cualquiera no puede usar el navegador para tocar la LAN.

    El navegador corre dentro de la red de casa. Sin este filtro, cualquier web
    que JARVIS abra puede lanzar peticiones al router, al NAS o al endpoint de
    metadatos de la nube desde una posición a la que desde fuera no se llega.
    """
    assert is_blocked_subresource(url) is True


@pytest.mark.parametrize(
    "url",
    ["https://cdn.example/foto.png", "http://ejemplo.com/x.js", "data:image/png;base64,AAA"],
)
def test_los_subrecursos_publicos_siguen_cargando(url):
    assert is_blocked_subresource(url) is False


# ── Los pasos ────────────────────────────────────────────────────────────────

def test_buscar_y_pulsar_el_resultado_encadenan(sesion):
    s, _ = sesion
    busqueda = asyncio.run(s.search("recetas de paella"))
    assert busqueda.title == "recetas de paella"
    assert [e["index"] for e in busqueda.links] == [1, 2]

    articulo = asyncio.run(s.click_link(1))
    assert articulo.url == "https://cocina.example/paella"
    assert "azafrán" in articulo.text


def test_pulsar_un_numero_que_no_existe_lo_dice_en_vez_de_romper(sesion):
    s, _ = sesion
    asyncio.run(s.search("recetas de paella"))
    vista = asyncio.run(s.click_link(99))
    assert "99" in vista.note


def test_no_se_puede_pulsar_antes_de_abrir_nada(sesion):
    s, _ = sesion
    with pytest.raises(BrowserUnavailable):
        asyncio.run(s.click_link(1))


def test_un_destino_privado_no_se_abre(sesion):
    s, _ = sesion
    with pytest.raises(ValueError):
        asyncio.run(s.open("https://192.168.68.201:8006/"))


def test_una_redireccion_hacia_la_red_privada_corta_la_navegacion(sesion):
    """La comprobación de antes miró la URL que pedimos, no adónde acabamos.

    Este es el agujero clásico: un dominio público que responde 302 hacia
    `192.168.x.x`. Sin la segunda comprobación, el navegador ya estaría dentro.
    """
    s, lanzador = sesion
    lanzador.page.paginas["https://inocente.example/"] = {
        "redirects_to": "https://192.168.68.1/admin",
        "title": "router",
        "text": "panel",
    }
    with pytest.raises(BrowserUnavailable):
        asyncio.run(s.open("https://inocente.example/"))
    # Y la pestaña no se queda ahí plantada.
    assert lanzador.page.historial[-1] == "about:blank"


def test_el_texto_largo_se_recorta_y_se_avisa(sesion):
    s, lanzador = sesion
    s.max_text_chars = 40
    lanzador.page.paginas["https://larga.example/"] = {"title": "Larga", "text": "x" * 500, "links": []}
    vista = asyncio.run(s.open("https://larga.example/"))
    assert len(vista.text) == 40
    assert "scroll" in vista.note


def test_no_se_admiten_descargas_ni_ventana_gigante(sesion):
    s, lanzador = sesion
    asyncio.run(s.open("https://cocina.example/paella"))
    assert lanzador.browser.opciones["accept_downloads"] is False
    assert lanzador.browser.opciones["viewport"] == {"width": 1280, "height": 800}


def test_cada_paso_deja_una_captura_nueva(sesion):
    """El HUD se entera de que hay algo nuevo que enseñar por este número."""
    s, _ = sesion
    asyncio.run(s.search("recetas de paella"))
    primera = s.screenshot_version
    asyncio.run(s.click_link(1))
    assert s.screenshot_version == primera + 1
    assert s.last_screenshot.startswith(b"PNG")


def test_el_clic_del_usuario_llega_a_la_pestana(sesion):
    s, lanzador = sesion
    asyncio.run(s.open("https://cocina.example/paella"))
    asyncio.run(s.click_point(640, 400))
    assert (640, 400) in lanzador.page.clics


def test_cerrar_suelta_el_navegador_y_la_captura(sesion):
    s, _ = sesion
    asyncio.run(s.open("https://cocina.example/paella"))
    assert s.last_screenshot
    asyncio.run(s.close())
    assert s.last_screenshot == b""


# ── La herramienta ───────────────────────────────────────────────────────────

def test_la_herramienta_devuelve_enlaces_numerados(sesion):
    s, _ = sesion
    tool = BrowseTool(s)
    salida = asyncio.run(tool.run(action="search", query="recetas de paella"))
    assert not salida.is_error
    cuerpo = json.loads(salida.content)
    assert cuerpo["links"][0]["index"] == 1
    assert cuerpo["screenshot_version"] >= 1


def test_una_accion_inventada_se_rechaza(sesion):
    s, _ = sesion
    assert asyncio.run(BrowseTool(s).run(action="hackear")).is_error


def test_un_destino_privado_vuelve_como_error_util_y_no_como_excepcion(sesion):
    """El modelo tiene que poder leer por qué falló y probar otra cosa."""
    s, _ = sesion
    salida = asyncio.run(BrowseTool(s).run(action="open", url="https://10.0.0.1/"))
    assert salida.is_error
    assert "privada" in salida.content


def test_pulsar_sin_numero_se_rechaza(sesion):
    s, _ = sesion
    assert asyncio.run(BrowseTool(s).run(action="click")).is_error

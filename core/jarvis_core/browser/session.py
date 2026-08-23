"""Un Chromium de verdad que JARVIS conduce paso a paso.

`fetch_web_page` baja el HTML y lo lee. Eso sirve para un artículo y no sirve
para nada que se monte con JavaScript, que hoy es casi todo: buscar en un sitio,
pulsar un resultado, pasar a la página siguiente. Aquí hay un navegador real
detrás, y JARVIS lo maneja por pasos: abre, mira, pulsa, vuelve a mirar.

Lo que lo hace defendible:

- **El destino se valida antes de cada navegación** con el mismo
  `validate_public_https_url` que protege las herramientas web, y **otra vez
  después**: una redirección puede acabar en `192.168.x.x` y sin la segunda
  comprobación el navegador ya estaría dentro de la red de casa.
- **Los subrecursos con destino privado se abortan** —imágenes, fetch, XHR—. Sin
  eso, una página cualquiera puede lanzar peticiones a la red local del usuario
  desde un navegador que corre justo dentro de ella.
- **No hay descargas** y no hay `file://`.
- Está **apagado por defecto**: necesita `JARVIS_BROWSER_ENABLED=true` y que
  Playwright y Chromium estén instalados. Sin eso, la herramienta ni se registra.

Playwright es opcional a propósito: Chromium pesa cientos de megas y no todo el
mundo lo quiere en su servidor. El módulo se importa igual sin él; lo que falla,
con un mensaje que dice qué instalar, es arrancar la sesión.
"""

from __future__ import annotations

import asyncio
import ipaddress
import urllib.parse
from dataclasses import dataclass, field

from jarvis_core.tools.builtin.web_tools import validate_public_https_url

#: Sufijos y nombres que nunca son un destino público, sin necesidad de resolver.
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home", ".arpa")
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})


class BrowserUnavailable(RuntimeError):
    """No hay navegador con el que trabajar, y se explica por qué."""


def is_blocked_subresource(url: str) -> bool:
    """¿Hay que abortar esta petición secundaria de la página?

    Se mira sin resolver DNS: esto corre en cada imagen, cada script y cada
    `fetch` de la página, y una resolución por petición haría el navegador
    inusable. Lo que corta es lo que se puede decidir mirando la URL: esquemas
    que no son web, direcciones IP privadas escritas tal cual y nombres locales.
    El destino principal —el que de verdad importa— sí se resuelve, en
    `validate_public_https_url`.
    """
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme in {"data", "blob", "about"}:
        return False
    if scheme not in {"http", "https"}:
        # file://, ftp://, chrome://… nada de eso tiene que cargar una página.
        return True
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return True
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        # Es un nombre de dominio, no una IP escrita a mano. Se deja pasar: la
        # alternativa es resolverlo aquí, en la ruta caliente de cada recurso.
        return False


@dataclass
class PageView:
    """Lo que se ve de la página tras un paso. Es lo que vuelve al modelo."""

    url: str = ""
    title: str = ""
    text: str = ""
    links: list[dict[str, object]] = field(default_factory=list)
    note: str = ""


class BrowserSession:
    """Una pestaña que sobrevive entre pasos.

    Que sea una sola y que persista es lo que permite «busca esto» → «pulsa el
    segundo» → «lee lo que pone»: sin estado compartido, cada paso empezaría de
    cero y no habría nada que pulsar.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 25.0,
        max_text_chars: int = 6000,
        max_links: int = 30,
        launcher: object | None = None,
    ) -> None:
        self.timeout_ms = int(timeout_seconds * 1000)
        self.max_text_chars = max_text_chars
        self.max_links = max_links
        self._launcher = launcher
        self._playwright = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        #: Última captura en PNG. Vive en memoria y no en disco: es efímera, y
        #: escribirla dejaría rastro de lo que el usuario mira.
        self.last_screenshot: bytes = b""
        self.screenshot_version = 0

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    async def _ensure_page(self):
        if self._page is not None:
            return self._page
        launcher = self._launcher
        if launcher is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - depende del entorno
                raise BrowserUnavailable(
                    "El navegador no está instalado. En el servidor: "
                    "pip install 'jarvis-core[browser]' && playwright install chromium"
                ) from exc
            self._playwright = await async_playwright().start()
            launcher = self._playwright.chromium
        self._browser = await launcher.launch(headless=True)
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            accept_downloads=False,
            java_script_enabled=True,
        )
        context.set_default_timeout(self.timeout_ms)
        await context.route("**/*", self._filter_request)
        self._page = await context.new_page()
        return self._page

    async def _filter_request(self, route, request) -> None:
        if is_blocked_subresource(request.url):
            await route.abort()
        else:
            await route.continue_()

    async def close(self) -> None:
        async with self._lock:
            for closable in (self._browser, self._playwright):
                if closable is None:
                    continue
                cerrar = getattr(closable, "close", None) or getattr(closable, "stop", None)
                if cerrar is not None:
                    try:
                        await cerrar()
                    except Exception:  # pragma: no cover - cerrar nunca debe romper
                        pass
            self._browser = None
            self._playwright = None
            self._page = None
            self.last_screenshot = b""

    # ── Pasos ────────────────────────────────────────────────────────────────

    async def open(self, url: str) -> PageView:
        destino = validate_public_https_url(url)
        async with self._lock:
            page = await self._ensure_page()
            await page.goto(destino, wait_until="domcontentloaded", timeout=self.timeout_ms)
            return await self._look(page)

    async def search(self, query: str) -> PageView:
        limpio = " ".join(str(query or "").split())[:200]
        if not limpio:
            raise ValueError("No hay nada que buscar.")
        return await self.open(
            "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": limpio})
        )

    async def click_link(self, index: int) -> PageView:
        """Pulsa un enlace por su número en la lista del paso anterior.

        Por número y no por selector CSS a propósito: un selector que el modelo
        se inventa falla la mayoría de las veces y no hay forma de saber por qué.
        La lista numerada es lo que acaba de ver.
        """
        async with self._lock:
            page = await self._require_page()
            enlaces = await self._links(page)
            elegido = next((e for e in enlaces if e["index"] == index), None)
            if elegido is None:
                vista = await self._look(page)
                vista.note = f"No hay ningún enlace con el número {index} en esta página."
                return vista
            destino = str(elegido["url"])
            try:
                validate_public_https_url(destino)
            except ValueError as exc:
                vista = await self._look(page)
                vista.note = f"Ese enlace no lleva a un destino público: {exc}"
                return vista
            await page.goto(destino, wait_until="domcontentloaded", timeout=self.timeout_ms)
            return await self._look(page)

    async def type_text(self, text: str, selector: str = "") -> PageView:
        async with self._lock:
            page = await self._require_page()
            objetivo = selector or "input:not([type=hidden]), textarea"
            await page.fill(objetivo, str(text)[:500])
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            return await self._look(page)

    async def scroll(self, amount: int = 700) -> PageView:
        async with self._lock:
            page = await self._require_page()
            await page.mouse.wheel(0, int(amount))
            return await self._look(page)

    async def back(self) -> PageView:
        async with self._lock:
            page = await self._require_page()
            await page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
            return await self._look(page)

    async def read(self) -> PageView:
        async with self._lock:
            page = await self._require_page()
            return await self._look(page)

    async def click_point(self, x: int, y: int) -> PageView:
        """Un clic donde el usuario pinchó en la captura del HUD."""
        async with self._lock:
            page = await self._require_page()
            await page.mouse.click(int(x), int(y))
            await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            return await self._look(page)

    # ── Lo que se mira después de cada paso ──────────────────────────────────

    async def _require_page(self):
        if self._page is None:
            raise BrowserUnavailable(
                "No hay ninguna página abierta todavía. Abre una con action='open' "
                "o busca con action='search'."
            )
        return self._page

    async def _settle(self, page) -> None:
        """Deja que la página acabe de pintarse antes de mirarla.

        Las navegaciones esperan a `domcontentloaded`, que en un sitio montado
        con JavaScript llega mucho antes de que haya nada en pantalla. Si se
        captura ahí, la foto es el esqueleto de carga —y como no se volvía a
        capturar nunca, la ventana se quedaba «cargando» para siempre aunque la
        página ya estuviera lista por dentro.

        Las dos esperas van con presupuesto y sin propagar el fallo: `networkidle`
        no llega nunca en sitios que sondean el servidor sin parar, y que no
        llegue no es un error, es que la página ya no va a estar más quieta.
        """
        for estado, presupuesto in (("load", 6000), ("networkidle", 3000)):
            try:
                await page.wait_for_load_state(estado, timeout=presupuesto)
            except Exception:
                pass

    async def _look(self, page) -> PageView:
        """Después de cada paso: dónde estamos, qué pone y qué se puede pulsar."""
        await self._settle(page)
        url = page.url
        # La redirección es el caso que importa: la comprobación de antes miró la
        # URL que pedimos, no adonde hemos acabado.
        try:
            validate_public_https_url(url)
        except ValueError as exc:
            await page.goto("about:blank")
            raise BrowserUnavailable(
                f"La navegación acabó en un destino no permitido y se cortó: {exc}"
            ) from exc

        titulo = await page.title()
        try:
            texto = await page.inner_text("body")
        except Exception:
            texto = ""
        texto = "\n".join(linea.strip() for linea in str(texto).splitlines() if linea.strip())
        recortado = len(texto) > self.max_text_chars
        vista = PageView(
            url=url,
            title=str(titulo or "")[:200],
            text=texto[: self.max_text_chars],
            links=await self._links(page),
        )
        if recortado:
            vista.note = "Texto recortado; usa action='scroll' para seguir leyendo."
        try:
            self.last_screenshot = await page.screenshot(type="png")
            self.screenshot_version += 1
        except Exception:
            pass
        return vista

    async def _links(self, page) -> list[dict[str, object]]:
        try:
            crudos = await page.eval_on_selector_all(
                "a[href]",
                """els => els.map(el => ({
                     text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 120),
                     url: el.href,
                   }))""",
            )
        except Exception:
            return []
        vistos: set[str] = set()
        salida: list[dict[str, object]] = []
        for crudo in crudos or []:
            url = str((crudo or {}).get("url") or "")
            texto = str((crudo or {}).get("text") or "")
            if not url.startswith("https://") or not texto or url in vistos:
                continue
            vistos.add(url)
            salida.append({"index": len(salida) + 1, "text": texto, "url": url})
            if len(salida) >= self.max_links:
                break
        return salida

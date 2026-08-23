"""Navegador real que JARVIS conduce: abrir, buscar, pulsar, leer."""

from __future__ import annotations

from jarvis_core.browser.session import (
    BrowserSession,
    BrowserUnavailable,
    is_blocked_subresource,
)

__all__ = [
    "BrowserSession",
    "BrowserUnavailable",
    "get_browser_session",
    "is_blocked_subresource",
    "reset_browser_session",
]

#: Un único navegador para todo el proceso.
#:
#: Cada conversación construye su propio registro de herramientas, y si cada una
#: arrancase su Chromium el servidor se quedaría sin memoria con tres pestañas
#: abiertas. Compartirlo además es lo que permite que el HUD sepa qué captura
#: enseñar: hay un solo sitio donde mirar.
_SESSION: BrowserSession | None = None


def get_browser_session(settings: object) -> BrowserSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = BrowserSession(
            timeout_seconds=float(getattr(settings, "browser_timeout_seconds", 25.0)),
            max_text_chars=int(getattr(settings, "browser_max_text_chars", 6000)),
        )
    return _SESSION


def reset_browser_session() -> None:
    """Suelta la sesión compartida. Para los tests, y para cerrar el gateway."""
    global _SESSION
    _SESSION = None

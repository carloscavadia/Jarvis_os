"""Estado compartido del gateway: servicios, configuración y autenticación.

`app.py` había crecido hasta ser la única puerta a todo: los servicios vivían
allí y cada endpoint nuevo se apilaba encima, así que partirlo por dominios
exigía primero un sitio neutral del que todos puedan colgar sin importarse
entre sí.

Los routers acceden a los servicios **a través del módulo** (`runtime.settings`,
`runtime.sessions`) y no importando los nombres sueltos. La diferencia importa:
un `from ... import voice_runtime` congela la referencia en el momento del
import, y sustituirlo después —como hacen los tests— dejaría al router usando el
original.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException
from jarvis_core.config import Settings

from jarvis_gateway.mqtt_bridge import MqttBridge
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.realtime_voice import RealtimeVoiceBroker
from jarvis_gateway.sessions import SessionManager
from jarvis_gateway.voice import VoiceRuntime

logger = logging.getLogger("jarvis.gateway")

settings = Settings.from_env()
sessions = SessionManager(settings)
mqtt_bridge = MqttBridge(settings, sessions)
notifier = Notifier(mqtt_bridge)
voice_runtime = VoiceRuntime(settings)
realtime_voice = RealtimeVoiceBroker(settings)


def valid_api_key(candidate: str | None) -> bool:
    return bool(
        settings.gateway_api_key
        and candidate
        and hmac.compare_digest(candidate, settings.gateway_api_key)
    )


def valid_connector_key(candidate: str | None) -> bool:
    return bool(
        settings.connectors_enabled
        and len(settings.n8n_webhook_token) >= 32
        and candidate
        and hmac.compare_digest(candidate, settings.n8n_webhook_token)
    )


async def require_api_key(
    x_jarvis_key: str | None = Header(default=None, alias="X-Jarvis-Key"),
) -> None:
    """Autentica clientes REST sin registrar ni devolver el secreto."""
    if not valid_api_key(x_jarvis_key):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")


async def require_connector_key(
    x_connector_key: str | None = Header(default=None, alias="X-Jarvis-Connector-Token"),
) -> None:
    """Autentica n8n sin concederle la clave maestra del gateway."""
    if not valid_connector_key(x_connector_key):
        raise HTTPException(status_code=401, detail="Credenciales de conector inválidas.")

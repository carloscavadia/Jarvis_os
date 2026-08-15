"""Empuje de avisos proactivos fuera del HUD.

Un recordatorio se ejecuta en el servidor, pero hasta ahora solo se entregaba a
los WebSocket conectados y al topic MQTT. Con el HUD cerrado —de noche, o desde
el móvil— el aviso se quedaba sin destinatario: la tarea corría, se registraba, y
el usuario no se enteraba de nada. Este módulo cierra ese hueco enviándolo
también a Telegram.

Por qué esto NO pide aprobación humana, a diferencia de `telegram.send`
-----------------------------------------------------------------------
La aprobación existe para que el modelo no decida por su cuenta escribir a
terceros. Aquí no decide nada el modelo: es el propio sistema avisando a su
dueño, al chat que el dueño configuró, con un texto que ya se le iba a entregar
por los otros canales. El destino **nunca** sale de la petición: se toma del
`default_chat_id` del módulo, así que ni el modelo ni un evento externo pueden
redirigirlo. Pedir confirmación para entregar una notificación al único usuario
del sistema convertiría la protección en un estorbo sin ganar nada.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.connectors.store import ConnectorStore

logger = logging.getLogger("jarvis.push")

PushSink = Callable[[str, str], Awaitable[None]]

#: Tope del cuerpo. Telegram corta en 4096 y un aviso largo no aporta más.
MAX_PUSH_CHARS = 1200


def find_telegram_module(store: ConnectorStore) -> str | None:
    """Nombre del primer módulo de Telegram listo para recibir avisos.

    Hace falta `default_chat_id`: sin destino fijo habría que elegirlo en cada
    envío, y elegir destinatario es justo lo que no debe hacerse sin que lo
    decida el usuario.
    """
    for module in store.list_public():
        if module["type"] != "telegram" or not module["enabled"]:
            continue
        record = store.get(module["name"])
        if record is not None and str(record.config.get("default_chat_id", "")).strip():
            return module["name"]
    return None


def build_telegram_push(
    store: ConnectorStore | None,
    *,
    timeout: float = 20.0,
) -> PushSink | None:
    """Devuelve el envío a Telegram, o None si no hay módulo configurado.

    Se resuelve en cada aviso y no al arrancar: un módulo puede darse de alta
    desde el panel con el gateway ya en marcha, y obligar a reiniciar para que
    empiecen a llegar las notificaciones sería una trampa difícil de adivinar.
    """
    if store is None:
        return None

    runtime = ConnectorRuntime(store, timeout=timeout)

    async def push(text: str, source: str) -> None:
        module = find_telegram_module(store)
        if module is None:
            return
        body = text.strip()[:MAX_PUSH_CHARS]
        if not body:
            return
        # El origen ayuda a distinguir un recordatorio de una alerta de conector
        # cuando llega al móvil sin más contexto.
        prefix = "⏰" if source.startswith("task") else "⚡"
        result = await runtime.invoke(
            module, "telegram.send", {"text": f"{prefix} {body}"}, write=True
        )
        if result.is_error:
            logger.warning("No pude enviar el aviso por Telegram: %s", result.content)

    return push

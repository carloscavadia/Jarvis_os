"""Notificador: entrega los avisos PROACTIVOS de JARVIS a todos los canales.

Cuando el scheduler dispara una tarea (o cuando JARVIS quiere avisar de algo por su cuenta),
el resultado se difunde a:
  - todos los WebSocket conectados (apps/web),
  - un topic MQTT de difusión, al que se suscriben los puntos de voz ESP32 de la casa.

Así, un recordatorio o alerta llega a todos los dispositivos tipo Alexa repartidos por casa.

Los dos canales anteriores exigen que alguien esté escuchando: el HUD abierto o
un ESP32 encendido. Por eso hay un tercero opcional, `push_sink`, para empujar
el aviso fuera de casa —hoy, Telegram—. Sin él, un recordatorio de madrugada se
ejecutaba correctamente y no llegaba a nadie.
"""

from __future__ import annotations

import json
import logging

from jarvis_gateway.mqtt_bridge import MqttBridge

logger = logging.getLogger("jarvis.notifier")

BROADCAST_TOPIC = "jarvis/broadcast"


class Notifier:
    def __init__(self, mqtt: MqttBridge, push_sink=None) -> None:
        self._mqtt = mqtt
        self._websockets: set = set()
        #: Canal externo opcional. Se asigna después de construir el notifier
        #: porque depende del almacén de conectores, que se abre más tarde.
        self.push_sink = push_sink

    def add_ws(self, ws) -> None:
        self._websockets.add(ws)

    def remove_ws(self, ws) -> None:
        self._websockets.discard(ws)

    async def broadcast(self, text: str, source: str = "jarvis", **extra) -> None:
        payload = {"type": "proactive", "source": source, "text": text, **extra}
        data = json.dumps(payload, ensure_ascii=False)

        # A los dispositivos de casa por MQTT.
        self._mqtt.publish(BROADCAST_TOPIC, data)

        # A los clientes WebSocket conectados.
        dead = []
        for ws in list(self._websockets):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — cliente caído; se limpia.
                dead.append(ws)
        for ws in dead:
            self._websockets.discard(ws)

        # Fuera de casa, si hay canal. Va al final y aislado a propósito: un
        # fallo de red con Telegram no puede impedir la entrega que ya se hizo
        # al HUD ni tumbar la tarea que originó el aviso.
        if self.push_sink is not None:
            try:
                await self.push_sink(text, source)
            except Exception:
                logger.exception("Falló el empuje del aviso proactivo")

        logger.info("Aviso proactivo difundido (%s): %s", source, text[:80])

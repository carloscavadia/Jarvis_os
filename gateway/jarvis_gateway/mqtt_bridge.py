"""Puente MQTT: conecta los dispositivos (ESP32) con el núcleo.

Convención de topics:
    jarvis/device/{device_id}/in      ← el dispositivo publica lo que quiere decir
    jarvis/device/{device_id}/out     → JARVIS publica la respuesta
    jarvis/device/{device_id}/status  ↔ heartbeat / estado

El puente se suscribe a `.../in`, pasa el texto por el orquestador de la sesión del
dispositivo, y publica la respuesta en `.../out`. Es ligero y encaja con el 4G del LilyGo.

Si el broker no está disponible, el puente lo registra y no tumba el gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from jarvis_core.config import Settings

from jarvis_gateway.sessions import SessionManager

logger = logging.getLogger("jarvis.mqtt")

TOPIC_IN = "jarvis/device/+/in"
#: Los nodos (máquinas con `jarvis-node`) anuncian y contestan por su propio espacio.
#: Un PC es otro dispositivo: mismo transporte, distinto vocabulario.
TOPIC_NODE = "jarvis/node/+/#"
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MqttBridge:
    def __init__(self, settings: Settings, sessions: SessionManager) -> None:
        self._settings = settings
        self._sessions = sessions
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._enabled = os.environ.get("JARVIS_MQTT_ENABLED", "false").lower() in {
            "1", "true", "yes", "on",
        }
        self._host = os.environ.get("JARVIS_MQTT_HOST", "localhost")
        self._port = int(os.environ.get("JARVIS_MQTT_PORT", "1883"))
        self._username = os.environ.get("JARVIS_MQTT_USERNAME", "")
        self._password = os.environ.get("JARVIS_MQTT_PASSWORD", "")
        self._tls = os.environ.get("JARVIS_MQTT_TLS", "false").lower() in {
            "1", "true", "yes", "on",
        }
        self._max_payload_chars = settings.mqtt_max_payload_chars

    def start(self) -> None:
        if not self._enabled:
            logger.info("Puente MQTT desactivado (JARVIS_MQTT_ENABLED=false).")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning("paho-mqtt no está instalado; puente MQTT desactivado.")
            return

        self._loop = asyncio.get_event_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self._username:
            client.username_pw_set(self._username, self._password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        if self._tls:
            client.tls_set()
        # connect_async + loop_start mantiene reintentos si el broker aún no está listo.
        client.connect_async(self._host, self._port, keepalive=60)
        client.loop_start()
        self._client = client
        logger.info("Puente MQTT iniciando conexión a %s:%s", self._host, self._port)

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def publish(self, topic: str, payload: str) -> None:
        """Publica un mensaje si hay conexión con el broker (no falla si no la hay)."""
        if self._client is not None:
            self._client.publish(topic, payload)

    # --- callbacks de paho (se ejecutan en el hilo de MQTT) ---

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            logger.warning("Conexión MQTT rechazada: %s", reason_code)
            return
        client.subscribe(TOPIC_IN)
        client.subscribe(TOPIC_NODE, qos=1)
        # El registro no puede publicar hasta que hay cliente; se le da ahora, no al
        # construirlo, porque antes de conectar no habría a dónde enviar.
        self._sessions.nodes.set_publish(self._publish_node)
        logger.info("Suscrito a %s y %s", TOPIC_IN, TOPIC_NODE)

    def _publish_node(self, topic: str, payload: str) -> None:
        if self._client is not None:
            self._client.publish(topic, payload, qos=1)

    def _handle_node_message(self, msg) -> bool:
        """Despacha un mensaje del espacio de nodos. Devuelve si era suyo."""
        parts = msg.topic.split("/")
        if len(parts) < 4 or parts[0] != "jarvis" or parts[1] != "node":
            return False
        node_id, kind = parts[2], parts[3]
        if not _DEVICE_ID_RE.fullmatch(node_id):
            logger.warning("Identificador de nodo inválido en '%s'", msg.topic)
            return True
        nodes = self._sessions.nodes
        if kind == "manifest":
            nodes.on_manifest(node_id, msg.payload)
        elif kind == "status":
            nodes.on_status(node_id, msg.payload)
        elif kind == "response":
            nodes.on_response(msg.payload)
        # `request` lo publicamos nosotros; verlo de vuelta es solo el eco de la
        # suscripción con comodín, no algo que atender.
        return True

    def _on_message(self, client, userdata, msg) -> None:
        if self._handle_node_message(msg):
            return
        # topic = jarvis/device/{device_id}/in
        parts = msg.topic.split("/")
        if len(parts) < 4:
            return
        device_id = parts[2]
        if not _DEVICE_ID_RE.fullmatch(device_id):
            logger.warning("Identificador MQTT inválido rechazado")
            return
        payload = msg.payload.decode(errors="replace").strip()
        if not payload:
            return
        if len(payload) > self._max_payload_chars:
            logger.warning("Payload MQTT demasiado grande rechazado para %s", device_id)
            return

        # El texto puede venir en crudo o como JSON {"text": "..."}.
        text = payload
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and "text" in data:
                text = str(data["text"])
        except (json.JSONDecodeError, ValueError):
            pass

        # Saltar del hilo MQTT al bucle asyncio para usar el orquestador.
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._handle(device_id, text, client), self._loop
        )

    async def _handle(self, device_id: str, text: str, client) -> None:
        session_id = f"device:{device_id}"
        state_topic = f"jarvis/device/{device_id}/state"
        out_topic = f"jarvis/device/{device_id}/out"

        # Estados para que el punto de voz anime su HUD mientras JARVIS trabaja.
        client.publish(state_topic, json.dumps({"state": "thinking"}))
        orchestrator = await self._sessions.get(session_id)
        reply = await orchestrator.send(text)
        client.publish(state_topic, json.dumps({"state": "speaking"}))
        client.publish(out_topic, json.dumps({"reply": reply.text}, ensure_ascii=False))
        client.publish(state_topic, json.dumps({"state": "idle"}))
        logger.info("Respondido a %s", device_id)

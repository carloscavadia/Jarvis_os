"""Transporte: conecta el nodo al broker del gateway.

**Solo salidas.** El nodo abre la conexión hacia el broker y se queda escuchando por
ella; no abre ningún puerto. Tu PC detrás de un router doméstico no necesita
redirecciones, y una máquina que no escucha no tiene superficie de entrada que atacar.

El manifiesto se publica **retenido**: cuando el gateway arranca o se reconecta,
encuentra ahí lo que este nodo ofrece sin tener que preguntarle ni esperar a que
vuelva a anunciarse.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis_node.agent import NodeAgent
from jarvis_node.protocol import (
    topic_manifest,
    topic_request,
    topic_response,
    topic_status,
)

logger = logging.getLogger("jarvis.node.mqtt")


class NodeTransport:
    """Puente entre el broker MQTT y el `NodeAgent`."""

    def __init__(
        self,
        agent: NodeAgent,
        host: str,
        port: int = 8883,
        *,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        keepalive: int = 60,
    ) -> None:
        self._agent = agent
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._keepalive = keepalive
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def node_id(self) -> str:
        return self._agent.capabilities.node_id

    def _build_client(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "Falta paho-mqtt. Instala el nodo con: pip install 'jarvis-node[mqtt]'"
            ) from exc

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"jarvis-node-{self.node_id}"
        )
        if self._username:
            client.username_pw_set(self._username, self._password)
        if self._use_tls:
            client.tls_set()
        # Testamento: si el nodo se cae o pierde la red, el broker publica su baja.
        # Sin esto, el gateway seguiría creyendo que la máquina está disponible y
        # encolando trabajo para alguien que ya no escucha.
        client.will_set(
            topic_status(self.node_id), '{"online": false}', qos=1, retain=True
        )
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._client = self._build_client()
        self._client.connect_async(self._host, self._port, self._keepalive)
        # `loop_start` reconecta solo tras un corte, que es justo lo que hace falta
        # en un portátil que se suspende o cambia de red.
        self._client.loop_start()
        logger.info("Nodo '%s' conectando a %s:%s", self.node_id, self._host, self._port)

    def stop(self) -> None:
        if self._client is None:
            return
        self._client.publish(topic_status(self.node_id), '{"online": false}', qos=1, retain=True)
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            logger.error("El broker rechazó la conexión: %s", reason_code)
            return
        client.subscribe(topic_request(self.node_id), qos=1)
        client.publish(
            topic_manifest(self.node_id), self._agent.manifest_json(), qos=1, retain=True
        )
        client.publish(topic_status(self.node_id), '{"online": true}', qos=1, retain=True)
        logger.info("Nodo '%s' conectado y anunciado", self.node_id)

    def _on_message(self, client, userdata, msg) -> None:
        """Callback de paho: corre en su propio hilo, no en el bucle asyncio.

        Por eso el trabajo se reenvía al bucle en vez de ejecutarse aquí: las
        operaciones son corrutinas, y `asyncio.run()` dentro del hilo de paho crearía
        un bucle nuevo por mensaje.
        """
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._process(msg.payload), self._loop)

    async def _process(self, payload: bytes) -> None:
        respuesta = await self._agent.handle_raw(payload)
        if respuesta is None or self._client is None:
            return
        self._client.publish(topic_response(self.node_id), respuesta, qos=1)

"""App FastAPI del gateway de JARVIS_OS.

Expone el núcleo a la red local (y a dispositivos) por tres vías:

  - REST      POST /chat            → petición/respuesta simple.
  - WebSocket /ws/{session_id}      → conversación bidireccional (apps/web).
  - MQTT      (puente en mqtt_bridge) → puntos de voz ESP32 tipo Alexa.

Además corre un SCHEDULER en segundo plano: JARVIS es reactivo (responde) y proactivo
(ejecuta tareas programadas por su cuenta y avisa a todos los dispositivos).

Pensado para correr como servicio 24/7 en un Ubuntu Server, accesible desde otros equipos
de la red. Escucha en 0.0.0.0. En reposo no consume IA: solo actúa ante peticiones o tareas.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from jarvis_core.config import Settings
from jarvis_core.tasks.scheduler import Scheduler
from jarvis_core.tasks.store import Task
from jarvis_gateway.mqtt_bridge import MqttBridge
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.sessions import SessionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.gateway")

settings = Settings.from_env()
sessions = SessionManager(settings)
mqtt_bridge = MqttBridge(settings, sessions)
notifier = Notifier(mqtt_bridge)


async def _on_task_fire(task: Task) -> None:
    """Se ejecuta cuando vence una tarea programada: lanza al agente y difunde el aviso."""
    orchestrator = await sessions.get("proactive")
    prompt = (
        f"[TAREA PROGRAMADA: {task.title}]\n{task.prompt}\n\n"
        f"Ejecútala ahora y redacta un aviso breve y claro para el usuario con el resultado."
    )
    reply = await orchestrator.send(prompt)
    await notifier.broadcast(reply.text, source="task", task_title=task.title)


scheduler = Scheduler(
    store=sessions.tasks,
    on_fire=_on_task_fire,
    poll_interval=settings.scheduler_poll_seconds,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_bridge.start()
    if settings.scheduler_enabled:
        scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        mqtt_bridge.stop()
        sessions.close()


app = FastAPI(title="JARVIS_OS Gateway", version="0.1.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = []
    session_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "persona": settings.persona_name,
        "provider": settings.llm_provider,
        "scheduler": "on" if settings.scheduler_enabled else "off",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Endpoint REST simple: envías un mensaje, recibes la respuesta del agente."""
    orchestrator = await sessions.get(req.session_id)
    reply = await orchestrator.send(req.message)
    return ChatResponse(
        reply=reply.text,
        tools_used=reply.tools_used,
        session_id=req.session_id,
    )


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket para conversación continua Y recepción de avisos proactivos."""
    await websocket.accept()
    orchestrator = await sessions.get(session_id)
    notifier.add_ws(websocket)  # recibirá también avisos proactivos
    logger.info("WebSocket conectado: sesión %s", session_id)
    # Estado inicial para que el HUD arranque en reposo.
    await websocket.send_json({"type": "state", "state": "idle"})
    try:
        while True:
            message = await websocket.receive_text()
            # Estados que la animación del HUD usa para reaccionar como el JARVIS de la peli.
            await websocket.send_json({"type": "state", "state": "listening"})
            await websocket.send_json({"type": "state", "state": "thinking"})
            reply = await orchestrator.send(message)
            # Emoción que JARVIS eligió para sí mismo (colorea el enjambre del HUD).
            await websocket.send_json({"type": "emotion", "emotion": reply.emotion})
            # Un flare de "ejecución" por cada herramienta usada (el HUD lo anima).
            for tool_name in reply.tools_used:
                if tool_name == "set_emotion":
                    continue
                await websocket.send_json({"type": "event", "event": "execution", "label": tool_name})
            await websocket.send_json({"type": "state", "state": "speaking"})
            await websocket.send_json(
                {"type": "reply", "reply": reply.text, "tools_used": reply.tools_used}
            )
            await websocket.send_json({"type": "state", "state": "idle"})
    except WebSocketDisconnect:
        logger.info("WebSocket desconectado: sesión %s", session_id)
    finally:
        notifier.remove_ws(websocket)


def serve() -> None:
    """Arranca el servidor escuchando en toda la red (0.0.0.0)."""
    import os
    import uvicorn

    host = os.environ.get("JARVIS_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("JARVIS_GATEWAY_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()

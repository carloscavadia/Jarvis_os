"""Gateway de red seguro para JARVIS_OS.

REST y WebSocket son canales remotos autenticados. El HUB conserva la presentación y
captura de entrada; el núcleo sigue siendo la única fuente de inteligencia y estado.
"""

from __future__ import annotations

import hmac
import logging
import re
from contextlib import asynccontextmanager

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from jarvis_core.config import Settings
from jarvis_core.tasks.scheduler import Scheduler
from jarvis_core.tasks.store import Task
from jarvis_core.voice import LocalVoiceError
from pydantic import BaseModel, Field

from jarvis_gateway.mqtt_bridge import MqttBridge
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.sessions import SessionManager
from jarvis_gateway.voice import VoiceRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.gateway")

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

settings = Settings.from_env()
sessions = SessionManager(settings)
mqtt_bridge = MqttBridge(settings, sessions)
notifier = Notifier(mqtt_bridge)
voice_runtime = VoiceRuntime(settings)


def _valid_api_key(candidate: str | None) -> bool:
    return bool(
        settings.gateway_api_key
        and candidate
        and hmac.compare_digest(candidate, settings.gateway_api_key)
    )


async def require_api_key(
    x_jarvis_key: str | None = Header(default=None, alias="X-Jarvis-Key"),
) -> None:
    """Autentica clientes REST sin registrar ni devolver el secreto."""
    if not _valid_api_key(x_jarvis_key):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")


async def _on_task_fire(task: Task) -> None:
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
    if not settings.gateway_api_key:
        raise RuntimeError(
            "Falta JARVIS_GATEWAY_API_KEY; el gateway se niega a arrancar sin autenticación."
        )
    mqtt_bridge.start()
    if settings.scheduler_enabled:
        scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        mqtt_bridge.stop()
        sessions.close()


app = FastAPI(title="JARVIS_OS Gateway", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.gateway_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Jarvis-Key"],
)


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=settings.gateway_max_message_chars,
    )
    session_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    session_id: str


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=settings.voice_max_text_chars)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "persona": settings.persona_name,
        "provider": settings.llm_provider,
        "scheduler": "on" if settings.scheduler_enabled else "off",
        "voice": "on" if settings.voice_enabled else "off",
    }


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Comprueba configuración mínima sin consumir una llamada al proveedor."""
    errors: list[str] = []
    provider = settings.llm_provider.lower()
    if not settings.gateway_api_key:
        errors.append("gateway_api_key")
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            errors.append("anthropic_api_key")
    elif provider in {"openai", "nvidia", "ollama", "compatible"}:
        if not settings.openai_model:
            errors.append("openai_model")
    else:
        errors.append("llm_provider")

    if errors:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "missing_or_invalid": errors},
        )
    return {"status": "ready", "provider": settings.llm_provider}


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        orchestrator = await sessions.get(req.session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    reply = await orchestrator.send(req.message)
    return ChatResponse(
        reply=reply.text,
        tools_used=reply.tools_used,
        session_id=req.session_id,
    )


@app.get("/voice/status", dependencies=[Depends(require_api_key)])
async def voice_status() -> dict[str, str | bool]:
    return voice_runtime.status()


@app.post("/voice/transcribe", dependencies=[Depends(require_api_key)])
async def transcribe_voice(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith(("audio/", "application/octet-stream")):
        raise HTTPException(status_code=415, detail="Se requiere contenido de audio.")
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > settings.voice_max_audio_bytes
    ):
        raise HTTPException(status_code=413, detail="Audio demasiado grande.")
    audio_buffer = bytearray()
    async for chunk in request.stream():
        audio_buffer.extend(chunk)
        if len(audio_buffer) > settings.voice_max_audio_bytes:
            raise HTTPException(status_code=413, detail="Audio demasiado grande.")
    audio = bytes(audio_buffer)
    if not audio:
        raise HTTPException(status_code=400, detail="Audio vacío.")
    if len(audio) > settings.voice_max_audio_bytes:
        raise HTTPException(status_code=413, detail="Audio demasiado grande.")
    try:
        text = await voice_runtime.transcribe(audio)
    except LocalVoiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error en el motor STT")
        raise HTTPException(status_code=503, detail="Motor STT no disponible.") from exc
    if not text:
        raise HTTPException(status_code=422, detail="No se detectó voz.")
    return {"text": text}


@app.post("/voice/synthesize", dependencies=[Depends(require_api_key)])
async def synthesize_voice(req: SpeechRequest) -> Response:
    try:
        audio = await voice_runtime.synthesize(req.text)
    except LocalVoiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error en el motor TTS")
        raise HTTPException(status_code=503, detail="Motor TTS no disponible.") from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    # Los navegadores no permiten cabeceras WebSocket arbitrarias; el HUB usa ?token=.
    if not _valid_api_key(websocket.query_params.get("token")):
        await websocket.close(code=1008, reason="Credenciales inválidas")
        return
    if not _SESSION_RE.fullmatch(session_id):
        await websocket.close(code=1008, reason="Identificador de sesión inválido")
        return

    try:
        orchestrator = await sessions.get(session_id)
    except RuntimeError:
        await websocket.close(code=1013, reason="Límite de sesiones alcanzado")
        return

    await websocket.accept()
    notifier.add_ws(websocket)
    logger.info("WebSocket conectado: sesión %s", session_id)
    await websocket.send_json({"type": "state", "state": "idle"})
    try:
        while True:
            message = (await websocket.receive_text()).strip()
            if not message:
                await websocket.send_json({"type": "error", "error": "Mensaje vacío"})
                continue
            if len(message) > settings.gateway_max_message_chars:
                await websocket.close(code=1009, reason="Mensaje demasiado grande")
                return

            await websocket.send_json({"type": "state", "state": "listening"})
            await websocket.send_json({"type": "state", "state": "thinking"})
            stream_started = False

            async def send_text_delta(delta: str) -> None:
                nonlocal stream_started
                if not stream_started:
                    stream_started = True
                    await websocket.send_json({"type": "state", "state": "speaking"})
                    await websocket.send_json({"type": "reply_start"})
                await websocket.send_json({"type": "reply_delta", "delta": delta})

            # El canal continúa disponible tras un fallo del proveedor.
            try:
                reply = await orchestrator.send(message, on_text_delta=send_text_delta)
            except Exception:
                logger.exception("Error procesando la sesión WebSocket %s", session_id)
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": "No pude completar la respuesta. Inténtalo de nuevo.",
                    }
                )
                await websocket.send_json({"type": "state", "state": "idle"})
                continue
            if not stream_started:
                await websocket.send_json({"type": "state", "state": "speaking"})
                await websocket.send_json({"type": "reply_start"})
                if reply.text:
                    await websocket.send_json(
                        {"type": "reply_delta", "delta": reply.text}
                    )
            await websocket.send_json({"type": "emotion", "emotion": reply.emotion})
            for tool_name in reply.tools_used:
                if tool_name != "set_emotion":
                    await websocket.send_json(
                        {"type": "event", "event": "execution", "label": tool_name}
                    )
            await websocket.send_json(
                {"type": "reply", "reply": reply.text, "tools_used": reply.tools_used}
            )
            await websocket.send_json({"type": "state", "state": "idle"})
    except WebSocketDisconnect:
        logger.info("WebSocket desconectado: sesión %s", session_id)
    finally:
        notifier.remove_ws(websocket)


def serve() -> None:
    import os

    import uvicorn

    host = os.environ.get("JARVIS_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("JARVIS_GATEWAY_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    serve()

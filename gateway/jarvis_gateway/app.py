"""Gateway de red seguro para JARVIS_OS.

REST y WebSocket son canales remotos autenticados. El HUB conserva la presentación y
captura de entrada; el núcleo sigue siendo la única fuente de inteligencia y estado.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.tasks.scheduler import Scheduler
from jarvis_core.tasks.store import Task
from jarvis_core.tools.base import ToolResult
from jarvis_core.tools.builtin.connectors import validate_connector_url
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


def _valid_connector_key(candidate: str | None) -> bool:
    return bool(
        settings.connectors_enabled
        and len(settings.n8n_webhook_token) >= 32
        and candidate
        and hmac.compare_digest(candidate, settings.n8n_webhook_token)
    )


def _approval_summary(name: str, arguments: dict[str, object]) -> str:
    if name == "install_package":
        return f"Instalar el paquete {arguments.get('package')} con {arguments.get('manager')}."
    if name == "update_file":
        content = str(arguments.get("content", ""))
        return (
            f"Modificar {arguments.get('path')} en modo {arguments.get('mode', 'replace')} "
            f"({len(content.encode('utf-8'))} bytes)."
        )
    if name == "run_python_file":
        return f"Ejecutar el script Python {arguments.get('path')}."
    if name == "run_connector_action":
        return f"Ejecutar la acción externa {arguments.get('action')} mediante n8n."
    if name == "run_connector_module_action":
        return f"Ejecutar {arguments.get('action')} mediante el módulo {arguments.get('connector')}."
    return f"Ejecutar la herramienta sensible {name}."


def _public_approval_arguments(arguments: dict[str, object]) -> dict[str, object]:
    """Evita reenviar contenidos completos o secretos innecesarios al cliente."""
    visible_keys = {
        "path",
        "mode",
        "manager",
        "package",
        "arguments",
        "query",
        "count",
        "url",
        "limit",
        "title",
        "format",
        "language",
        "keep_open",
        "at",
        "delay_seconds",
        "repeat_seconds",
        "task_id",
        "emotion",
        "action",
        "connector",
    }
    public = {key: value for key, value in arguments.items() if key in visible_keys}
    if isinstance(public.get("url"), str):
        parsed_url = urlsplit(public["url"])
        public["url"] = urlunsplit(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", "")
        )
    if "content" in arguments:
        content = str(arguments["content"])
        public["content_bytes"] = len(content.encode("utf-8"))
    if "payload" in arguments:
        try:
            payload = json.dumps(arguments["payload"], ensure_ascii=False)
        except (TypeError, ValueError):
            payload = ""
        public["payload_bytes"] = len(payload.encode("utf-8"))
        public["payload_preview"] = _redact_connector_payload(arguments["payload"])
    return public


def _redact_connector_payload(value: object, depth: int = 0) -> object:
    """Ofrece contexto de aprobación y oculta campos típicos de credenciales."""
    if depth > 4:
        return "[límite de profundidad]"
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                redacted["…"] = "[campos adicionales omitidos]"
                break
            normalized = str(key).lower().replace("-", "_")
            if any(
                secret in normalized
                for secret in (
                    "token",
                    "password",
                    "secret",
                    "api_key",
                    "authorization",
                )
            ):
                redacted[str(key)] = "[oculto]"
            else:
                redacted[str(key)] = _redact_connector_payload(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact_connector_payload(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:500] + ("…" if len(value) > 500 else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _public_proactive_event(event: dict[str, object]) -> dict[str, object]:
    public = dict(event)
    public["payload"] = _redact_connector_payload(event.get("payload", {}))
    return public


def _workspace_presentation(
    name: str, arguments: dict[str, object]
) -> dict[str, object] | None:
    """Construye una presentación segura para que el HUD la renderice como texto."""
    if name != "show_in_workspace":
        return None
    title = arguments.get("title")
    content = arguments.get("content")
    if not isinstance(title, str) or not isinstance(content, str):
        return None
    presentation_format = arguments.get("format", "text")
    if presentation_format not in {"text", "code", "json", "table", "markdown"}:
        presentation_format = "text"
    language = arguments.get("language", "")
    return {
        "title": title.strip()[:100] or "Presentación de JARVIS",
        "content": content[:12000],
        "format": presentation_format,
        "language": str(language)[:32],
        "keep_open": arguments.get("keep_open", True) is not False,
    }


def _goal_progress(name: str, result: ToolResult | None) -> dict[str, object] | None:
    if (
        name
        not in {
            "create_goal_plan",
            "get_goal_plan",
            "update_goal_step",
            "close_goal_plan",
        }
        or result is None
    ):
        return None
    try:
        progress = json.loads(result.content)
    except (json.JSONDecodeError, TypeError):
        return None
    return progress if isinstance(progress, dict) and "goal_id" in progress else None


def _python_preview(name: str, arguments: dict[str, object]) -> str | None:
    path_value = arguments.get("path")
    if not isinstance(path_value, str) or not path_value.lower().endswith(".py"):
        return None
    if name in {"create_file", "update_file"}:
        content = arguments.get("content")
        return str(content)[:12000] if isinstance(content, str) else None
    if name != "run_python_file":
        return None
    root = Path(settings.workspace_root).resolve()
    script = (root / path_value).resolve(strict=False)
    try:
        script.relative_to(root)
    except ValueError:
        return None
    try:
        if not script.is_file() or script.stat().st_size > 12000:
            return None
        return script.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


async def require_api_key(
    x_jarvis_key: str | None = Header(default=None, alias="X-Jarvis-Key"),
) -> None:
    """Autentica clientes REST sin registrar ni devolver el secreto."""
    if not _valid_api_key(x_jarvis_key):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")


async def require_connector_key(
    x_connector_key: str | None = Header(
        default=None, alias="X-Jarvis-Connector-Token"
    ),
) -> None:
    """Autentica n8n sin concederle la clave maestra del gateway."""
    if not _valid_connector_key(x_connector_key):
        raise HTTPException(
            status_code=401, detail="Credenciales de conector inválidas."
        )


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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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


class ConnectorChatRequest(BaseModel):
    connector: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    external_user: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=settings.gateway_max_message_chars)


class ConnectorEventRequest(BaseModel):
    connector: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    event: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.:-]+$")
    text: str = Field(min_length=1, max_length=settings.gateway_max_message_chars)
    title: str = Field(default="", max_length=120)
    severity: str = Field(default="info", pattern=r"^(info|warning|critical)$")
    policy: str = Field(
        default="auto", pattern=r"^(auto|notify|create_goal|request_action)$"
    )
    action: str = Field(default="", max_length=128, pattern=r"^[a-zA-Z0-9_.:-]*$")
    payload: dict[str, object] = Field(default_factory=dict)


class ProactiveDecisionRequest(BaseModel):
    approved: bool


class ConnectorModuleRequest(BaseModel):
    name: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]+$")
    type: str = Field(pattern=r"^(n8n|home_assistant)$")
    url: str = Field(min_length=8, max_length=2000)
    token: str = Field(min_length=8, max_length=8192)
    services: list[str] = Field(default_factory=list, max_length=64)
    read_actions: list[str] = Field(default_factory=list, max_length=128)
    write_actions: list[str] = Field(default_factory=list, max_length=128)
    enabled: bool = True


class ConnectorModuleTestRequest(BaseModel):
    name: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]+$")


class GoalControlRequest(BaseModel):
    action: str = Field(pattern=r"^(resume|block|cancel)$")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "persona": settings.persona_name,
        "provider": settings.llm_provider,
        "scheduler": "on" if settings.scheduler_enabled else "off",
        "voice": "on" if settings.voice_enabled else "off",
        "connectors": "on" if settings.connectors_enabled else "off",
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
    if settings.connectors_enabled:
        legacy_n8n_configured = bool(
            settings.n8n_webhook_url or settings.n8n_webhook_token
        )
        if legacy_n8n_configured:
            if not settings.n8n_webhook_url:
                errors.append("n8n_webhook_url")
            else:
                try:
                    validate_connector_url(settings.n8n_webhook_url)
                except ValueError:
                    errors.append("n8n_webhook_url")
            if len(settings.n8n_webhook_token) < 32:
                errors.append("n8n_webhook_token")
        if not legacy_n8n_configured and not settings.connector_master_key:
            errors.append("connector_master_key")

    if errors:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "missing_or_invalid": errors},
        )
    return {"status": "ready", "provider": settings.llm_provider}


def _require_connector_store():
    if sessions.connector_store is None:
        raise HTTPException(
            status_code=503,
            detail="Configura JARVIS_CONNECTOR_MASTER_KEY para administrar módulos.",
        )
    return sessions.connector_store


@app.get("/connector-modules", dependencies=[Depends(require_api_key)])
async def list_connector_modules() -> list[dict[str, object]]:
    return _require_connector_store().list_public()


@app.put("/connector-modules/{name}", dependencies=[Depends(require_api_key)])
async def register_connector_module(
    name: str, req: ConnectorModuleRequest
) -> dict[str, object]:
    if name != req.name:
        raise HTTPException(
            status_code=400, detail="El nombre de la ruta y el cuerpo no coincide."
        )
    try:
        url = validate_connector_url(req.url)
        _require_connector_store().upsert(
            req.name,
            req.type,
            {
                "url": url,
                "services": sorted(set(req.services)),
                "read_actions": sorted(set(req.read_actions)),
                "write_actions": sorted(set(req.write_actions)),
            },
            {"token": req.token},
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"name": req.name, "type": req.type, "enabled": req.enabled, "stored": True}


@app.post("/connector-modules/test", dependencies=[Depends(require_api_key)])
async def test_connector_module(req: ConnectorModuleTestRequest) -> dict[str, object]:
    store = _require_connector_store()
    runtime = ConnectorRuntime(
        store,
        timeout=settings.connector_timeout_seconds,
        max_payload_bytes=settings.connector_max_payload_bytes,
        max_response_bytes=settings.connector_max_response_bytes,
    )
    result = await runtime.test(req.name)
    if result.is_error:
        raise HTTPException(status_code=502, detail=result.content)
    return {"name": req.name, "ok": True, "message": result.content[:1000]}


@app.delete("/connector-modules/{name}", dependencies=[Depends(require_api_key)])
async def delete_connector_module(name: str) -> dict[str, object]:
    return {"name": name, "deleted": _require_connector_store().delete(name)}


@app.get("/goals/current", dependencies=[Depends(require_api_key)])
async def current_goal() -> dict[str, object]:
    goal = sessions.goals.current()
    if goal is None:
        return {"active": False}
    return {"active": goal.status == "active", "goal": json.loads(sessions.goals.serialize(goal))}


@app.post("/goals/{goal_id}/control", dependencies=[Depends(require_api_key)])
async def control_goal(goal_id: int, req: GoalControlRequest) -> dict[str, object]:
    try:
        if req.action == "resume":
            goal = sessions.goals.resume(goal_id)
        else:
            goal = sessions.goals.set_status(
                goal_id, "blocked" if req.action == "block" else "cancelled"
            )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return json.loads(sessions.goals.serialize(goal))


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


@app.post(
    "/connectors/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_connector_key)],
)
async def connector_chat(req: ConnectorChatRequest) -> ChatResponse:
    """Procesa una conversación externa sin exponer su identificador como sesión."""
    identity = f"{req.connector}:{req.external_user}".encode()
    identity_hash = hashlib.sha256(identity).hexdigest()
    if (
        not settings.connector_chat_enabled
        or identity_hash not in settings.connector_allowed_user_hashes
    ):
        raise HTTPException(status_code=403, detail="Usuario externo no autorizado.")
    session_id = f"connector-{identity_hash[:24]}"
    try:
        orchestrator = await sessions.get(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    reply = await orchestrator.send(
        f"[Mensaje recibido mediante {req.connector}]\n{req.message}"
    )
    return ChatResponse(
        reply=reply.text,
        tools_used=reply.tools_used,
        session_id=session_id,
    )


@app.post("/connectors/events", dependencies=[Depends(require_connector_key)])
async def connector_event(req: ConnectorEventRequest) -> dict[str, object]:
    """Clasifica, audita y difunde un evento autenticado de un conector."""
    policy = req.policy
    if policy == "auto":
        if req.action:
            policy = "request_action"
        elif req.severity == "critical":
            policy = "create_goal"
        else:
            policy = "notify"
    if policy == "request_action" and not req.action:
        raise HTTPException(status_code=422, detail="request_action requiere action.")
    if policy == "request_action":
        store = _require_connector_store()
        record = store.get(req.connector)
        if (
            record is None
            or not record.enabled
            or req.action not in record.config.get("write_actions", [])
        ):
            raise HTTPException(
                status_code=422,
                detail="La acción solicitada no está permitida para este módulo.",
            )

    status = "pending_approval" if policy == "request_action" else "accepted"
    event_id = sessions.proactive_events.create(
        connector=req.connector,
        event_type=req.event,
        title=req.title,
        text=req.text,
        severity=req.severity,
        policy=policy,
        status=status,
        action=req.action,
        payload=req.payload,
    )
    goal = None
    final_status = status
    if policy == "create_goal":
        try:
            goal_id = sessions.goals.create(
                req.title or f"Atender evento {req.event}",
                req.text,
                [
                    {"title": "Evaluar el evento", "verification": "Causa verificada"},
                    {"title": "Resolver o escalar", "verification": "Resultado confirmado"},
                ],
            )
            created_goal = sessions.goals.get(goal_id)
            assert created_goal is not None
            goal = json.loads(sessions.goals.serialize(created_goal))
            sessions.proactive_events.transition(
                event_id,
                from_status="accepted",
                to_status="goal_created",
                result=f"Objetivo #{goal_id}",
            )
            final_status = "goal_created"
        except ValueError as exc:
            sessions.proactive_events.transition(
                event_id,
                from_status="accepted",
                to_status="blocked",
                result=str(exc),
            )
            final_status = "blocked"

    await notifier.broadcast(
        req.text,
        source=f"connector:{req.connector}",
        connector=req.connector,
        event=req.event,
        title=req.title,
        severity=req.severity,
        policy=policy,
        status=final_status,
        event_id=event_id,
        action=req.action,
        payload=_redact_connector_payload(req.payload),
        goal=goal,
    )
    return {"status": final_status, "event_id": event_id}


@app.get("/proactive/events", dependencies=[Depends(require_api_key)])
async def proactive_events(limit: int = 50) -> list[dict[str, object]]:
    return [
        _public_proactive_event(event)
        for event in sessions.proactive_events.list_recent(limit)
    ]


@app.post(
    "/proactive/events/{event_id}/decision", dependencies=[Depends(require_api_key)]
)
async def decide_proactive_event(
    event_id: int, req: ProactiveDecisionRequest
) -> dict[str, object]:
    event = sessions.proactive_events.get(event_id)
    if event is None or event["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail="El evento no existe o ya fue resuelto.")
    if not req.approved:
        resolved = sessions.proactive_events.transition(
            event_id,
            from_status="pending_approval",
            to_status="denied",
            result="Acción denegada por el usuario.",
        )
        await notifier.broadcast(
            "Acción proactiva denegada.",
            source="proactive",
            event_id=event_id,
            status="denied",
            action=event["action"],
        )
        return _public_proactive_event(resolved)

    sessions.proactive_events.transition(
        event_id, from_status="pending_approval", to_status="running"
    )
    runtime = ConnectorRuntime(
        _require_connector_store(),
        timeout=settings.connector_timeout_seconds,
        max_payload_bytes=settings.connector_max_payload_bytes,
        max_response_bytes=settings.connector_max_response_bytes,
    )
    try:
        result = await runtime.invoke(
            str(event["connector"]),
            str(event["action"]),
            event["payload"],
            write=True,
        )
    except Exception:
        logger.exception("Falló la acción proactiva #%s", event_id)
        result = ToolResult("La acción externa falló de forma inesperada.", is_error=True)
    resolved = sessions.proactive_events.transition(
        event_id,
        from_status="running",
        to_status="failed" if result.is_error else "completed",
        result=result.content,
    )
    await notifier.broadcast(
        result.content,
        source="proactive",
        event_id=event_id,
        status=resolved["status"],
        action=event["action"],
    )
    return _public_proactive_event(resolved)


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

    async def request_confirmation(name: str, arguments: dict[str, object]) -> bool:
        approval_id = secrets.token_urlsafe(8)
        deadline = asyncio.get_running_loop().time() + settings.approval_timeout_seconds
        await websocket.send_json(
            {
                "type": "approval_required",
                "approval_id": approval_id,
                "tool": name,
                "summary": _approval_summary(name, arguments),
                "arguments": _public_approval_arguments(arguments),
                "timeout_seconds": settings.approval_timeout_seconds,
            }
        )
        for _ in range(3):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "approval_resolved",
                        "approval_id": approval_id,
                        "approved": False,
                        "reason": "timeout",
                    }
                )
                return False

            approved: bool | None = None
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get("type") == "approval"
                and payload.get("approval_id") == approval_id
                and isinstance(payload.get("approved"), bool)
            ):
                approved = payload["approved"]
            else:
                normalized = raw.strip().lower()
                if normalized == f"aprobar {approval_id}".lower():
                    approved = True
                elif normalized == f"denegar {approval_id}".lower():
                    approved = False

            if approved is None:
                await websocket.send_json(
                    {
                        "type": "approval_invalid",
                        "approval_id": approval_id,
                        "message": "Responde APROBAR o DENEGAR desde el HUD.",
                    }
                )
                continue

            await websocket.send_json(
                {
                    "type": "approval_resolved",
                    "approval_id": approval_id,
                    "approved": approved,
                    "reason": "user",
                }
            )
            return approved
        await websocket.send_json(
            {
                "type": "approval_resolved",
                "approval_id": approval_id,
                "approved": False,
                "reason": "invalid_or_timeout",
            }
        )
        return False

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

            async def send_tool_event(
                phase: str,
                name: str,
                arguments: dict[str, object],
                result: ToolResult | None,
            ) -> None:
                payload: dict[str, object] = {
                    "type": "tool_event",
                    "phase": phase,
                    "tool": name,
                    "arguments": _public_approval_arguments(arguments),
                }
                preview = _python_preview(name, arguments)
                if preview is not None:
                    payload["code"] = preview
                    payload["language"] = "python"
                presentation = _workspace_presentation(name, arguments)
                if presentation is not None:
                    payload["presentation"] = presentation
                if result is not None:
                    if presentation is None:
                        payload["output"] = result.content[:12000]
                    payload["is_error"] = result.is_error
                    goal = _goal_progress(name, result)
                    if goal is not None:
                        payload["goal"] = goal
                await websocket.send_json(payload)

            # El canal continúa disponible tras un fallo del proveedor.
            try:
                reply = await orchestrator.send(
                    message,
                    on_text_delta=send_text_delta,
                    confirm=request_confirmation,
                    on_tool_event=send_tool_event,
                )
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

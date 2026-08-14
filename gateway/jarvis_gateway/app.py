"""Gateway de red seguro para JARVIS_OS.

REST y WebSocket son canales remotos autenticados. El HUB conserva la presentación y
captura de entrada; el núcleo sigue siendo la única fuente de inteligencia y estado.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import secrets
import urllib.request
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
from fastapi.responses import StreamingResponse
from jarvis_core.agent.emotion import EmotionState
from jarvis_core.config import Settings
from jarvis_core.connectors.runtime import ConnectorRuntime
from jarvis_core.music.navidrome import build_navidrome_client
from jarvis_core.tasks.scheduler import Scheduler
from jarvis_core.tasks.store import Task
from jarvis_core.tools.base import ToolResult
from jarvis_core.tools.builtin.connectors import validate_connector_url
from jarvis_core.tools.builtin.filesystem import WorkspaceGuard
from jarvis_core.voice import LocalVoiceError, WakeWordDetector
from pydantic import BaseModel, Field

from jarvis_gateway import realtime_session as realtime_module
from jarvis_gateway.mqtt_bridge import MqttBridge
from jarvis_gateway.notifier import Notifier
from jarvis_gateway.realtime_session import RealtimeConversation
from jarvis_gateway.realtime_voice import RealtimeVoiceBroker, RealtimeVoiceError
from jarvis_gateway.sessions import SessionManager
from jarvis_gateway.voice import VoiceRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.gateway")

_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

#: Capacidades que este gateway sabe servir. El HUD las consulta para avisar
#: cuando pide algo que esta versión del servidor todavía no ofrece.
GATEWAY_FEATURES = frozenset(
    {"wakeword", "realtime_conversation", "music_stream", "music_cover"}
)

settings = Settings.from_env()
sessions = SessionManager(settings)
mqtt_bridge = MqttBridge(settings, sessions)
notifier = Notifier(mqtt_bridge)
voice_runtime = VoiceRuntime(settings)
realtime_voice = RealtimeVoiceBroker(settings)
# Escuchas de activación abiertas. Cada una carga su propio detector, así que el
# límite acota tanto la memoria como la CPU dedicada a la escucha permanente.
_wake_streams = 0
# Conversaciones Realtime abiertas. Estas sí cuestan dinero mientras viven, así
# que el límite es la última barrera contra un gasto inesperado.
_realtime_sessions = 0


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


def _music_command(name: str, result: ToolResult | None) -> dict[str, object] | None:
    """Traduce el resultado de una herramienta de música en orden para el HUD."""
    if name not in {"play_music", "control_music"} or result is None or result.is_error:
        return None
    try:
        payload = json.loads(result.content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if name == "control_music":
        command = payload.get("command")
        return {"command": command} if isinstance(command, str) else None
    queue = payload.get("queue")
    if not isinstance(queue, list) or not queue:
        return None
    songs = [song for song in queue if isinstance(song, dict) and song.get("id")]
    if not songs:
        return None
    return {
        "command": "play",
        "source": str(payload.get("source", "")),
        "queue": songs[:50],
    }


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
    if settings.voice_enabled and settings.wakeword_enabled:
        # Descarga y compila el modelo de activación ahora (~6 MB) para que la
        # primera conexión de un HUB no espere. Un fallo aquí no impide arrancar:
        # el resto del gateway funciona igual sin escucha permanente.
        async def _warm_up_wakeword() -> None:
            try:
                await asyncio.to_thread(
                    WakeWordDetector(settings.wakeword_model).warm_up
                )
            except Exception:
                logger.exception("No se pudo precargar el modelo de activación")

        asyncio.create_task(_warm_up_wakeword())
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
    token: str = Field(default="", max_length=8192)
    services: list[str] = Field(default_factory=list, max_length=64)
    read_actions: list[str] = Field(default_factory=list, max_length=128)
    write_actions: list[str] = Field(default_factory=list, max_length=128)
    enabled: bool = True


class ConnectorModuleTestRequest(BaseModel):
    name: str = Field(min_length=2, max_length=32, pattern=r"^[a-z][a-z0-9_-]+$")


class GoalControlRequest(BaseModel):
    action: str = Field(pattern=r"^(resume|block|cancel)$")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "persona": settings.persona_name,
        "provider": settings.llm_provider,
        "scheduler": "on" if settings.scheduler_enabled else "off",
        "voice": "on" if settings.voice_enabled else "off",
        "realtime_voice": "on" if realtime_voice.enabled else "off",
        "connectors": "on" if settings.connectors_enabled else "off",
        # Permite detectar de un vistazo que el HUD y el gateway van desparejados,
        # que es la causa típica de que una función "esté" pero no haga nada.
        "music": "on" if settings.navidrome_url else "off",
        "features": sorted(GATEWAY_FEATURES),
    }


@app.get("/hud")
async def serve_hud() -> Response:
    """Sirve el HUD desde el propio gateway.

    Así el HUD y el servidor viajan siempre juntos: no puede haber una copia
    suelta del `index.html` en otra máquina que se quede atrás y provoque que
    una función «esté» pero no haga nada.

    Ábrelo por un túnel para conservar el contexto seguro que exige el micrófono:
        ssh -L 8080:localhost:8080 usuario@servidor
        http://127.0.0.1:8080/hud
    """
    path = Path(settings.hud_path)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No encuentro el HUD en {settings.hud_path}.",
        )
    return Response(
        content=path.read_bytes(),
        media_type="text/html; charset=utf-8",
        # Sin esto el navegador serviría la versión anterior tras cada
        # actualización, que es justo el problema que este endpoint resuelve.
        headers={"Cache-Control": "no-store"},
    )


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
    elif provider in {"openai_responses", "openai-native", "openai_native"}:
        if not settings.openai_responses_api_key:
            errors.append("openai_api_key")
        if not settings.openai_responses_model:
            errors.append("openai_responses_model")
    elif provider in {"openai", "nvidia", "ollama", "compatible"}:
        if not settings.openai_model:
            errors.append("openai_model")
    else:
        errors.append("llm_provider")
    if (
        settings.openai_realtime_enabled
        and not settings.openai_responses_api_key
        and "openai_api_key" not in errors
    ):
        errors.append("openai_api_key")
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
        store = _require_connector_store()
        secret = req.token
        if not secret:
            existing = store.get(req.name)
            if existing is None:
                raise ValueError("La clave o token es obligatorio al crear el módulo.")
            secret = str(existing.config.get("_secrets", {}).get("token", ""))
        if len(secret) < 8:
            raise ValueError("La clave o token debe tener al menos 8 caracteres.")
        config: dict[str, object] = {
            "url": url,
            "services": sorted(set(req.services)),
            "read_actions": sorted(set(req.read_actions)),
            "write_actions": sorted(set(req.write_actions)),
        }
        store.upsert(
            req.name,
            req.type,
            config,
            {"token": secret},
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Las herramientas del módulo deben aparecer ya, sin reiniciar el gateway.
    await sessions.refresh_tools()
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
    deleted = _require_connector_store().delete(name)
    if deleted:
        await sessions.refresh_tools()
    return {"name": name, "deleted": deleted}


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
async def voice_status() -> dict[str, object]:
    status: dict[str, object] = dict(voice_runtime.status())
    status["realtime"] = realtime_voice.status()
    status["wakeword"] = {
        "enabled": settings.voice_enabled and settings.wakeword_enabled,
        "model": settings.wakeword_model,
        "sample_rate": WakeWordDetector.SAMPLE_RATE,
        "frame_samples": WakeWordDetector.FRAME_SAMPLES,
        "engine": "openwakeword",
    }
    return status


@app.post("/voice/realtime-token", dependencies=[Depends(require_api_key)])
async def create_realtime_voice_token() -> dict[str, object]:
    """Entrega una credencial breve; nunca expone OPENAI_API_KEY."""
    try:
        return await realtime_voice.create_client_secret()
    except RealtimeVoiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("No se pudo crear la sesión OpenAI Realtime")
        raise HTTPException(
            status_code=503, detail="Voz OpenAI Realtime no disponible."
        ) from exc


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


_MEDIA_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


async def _proxy_media(endpoint: str, params: dict[str, str]):
    """Sirve audio o carátula desde Navidrome sin exponer sus credenciales.

    El navegador no puede poner cabeceras en `<audio src>`, así que la
    alternativa sería mandarle la contraseña del servidor de música. Con el proxy
    solo circula la clave del gateway, que es la que el HUD ya tiene.
    """
    client = build_navidrome_client(settings)
    if client is None:
        raise HTTPException(
            status_code=404, detail="No hay servidor de música configurado."
        )
    url = client.endpoint_url(endpoint, params)

    def fetch():
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "JARVIS-OS/0.3 navidrome"}
        )
        return urllib.request.urlopen(
            request, timeout=settings.navidrome_timeout_seconds
        )

    try:
        upstream = await asyncio.to_thread(fetch)
    except Exception as exc:
        logger.warning("No pude obtener %s del servidor de música: %s", endpoint, exc)
        raise HTTPException(
            status_code=502, detail="El servidor de música no respondió."
        ) from exc

    media_type = upstream.headers.get_content_type() or "application/octet-stream"

    def chunks():
        # Se transmite por partes: una canción no cabe —ni debe caber— en los
        # límites de respuesta pensados para JSON.
        try:
            while True:
                block = upstream.read(64 * 1024)
                if not block:
                    return
                yield block
        finally:
            upstream.close()

    return StreamingResponse(
        chunks(),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/music/stream/{song_id}")
async def music_stream(song_id: str, token: str = ""):
    # El `<audio>` del navegador no admite cabeceras: la clave viaja por query,
    # igual que ya hace el WebSocket del HUD.
    if not _valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    if not _MEDIA_ID_RE.fullmatch(song_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    return await _proxy_media("stream", {"id": song_id})


@app.get("/music/cover/{cover_id}")
async def music_cover(cover_id: str, token: str = "", size: int = 256):
    if not _valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    if not _MEDIA_ID_RE.fullmatch(cover_id):
        raise HTTPException(status_code=422, detail="Identificador inválido.")
    return await _proxy_media(
        "getCoverArt", {"id": cover_id, "size": str(max(32, min(size, 1024)))}
    )


class WorkspaceCreateRequest(BaseModel):
    path: str
    is_dir: bool = False
    content: str | None = None


def _get_workspace_guard() -> WorkspaceGuard:
    return WorkspaceGuard(settings.workspace_root, settings.max_file_bytes)


@app.get("/workspace/tree", dependencies=[Depends(require_api_key)])
async def workspace_tree(path: str = "."):
    try:
        guard = _get_workspace_guard()
        raw = (path or ".").strip()
        
        resolved = None
        try:
            candidate = guard.resolve(raw, allow_root=True)
            if candidate.exists() and candidate.is_dir():
                resolved = candidate
        except Exception:
            pass

        if resolved is None:
            resolved = guard.resolve(".", allow_root=True)

        items = []
        try:
            raw_entries = list(resolved.iterdir())
        except Exception as exc:
            logger.warning("Error leyendo directorio %s: %s", resolved, exc)
            disp_p = "."
            try:
                disp_p = guard.display(resolved)
            except Exception:
                pass
            return {"path": disp_p, "items": []}

        def safe_sort_key(item: Path) -> tuple[bool, str]:
            is_directory = False
            try:
                is_directory = item.is_dir()
            except Exception:
                pass
            return (not is_directory, item.name.lower())

        entries = sorted(raw_entries, key=safe_sort_key)

        for entry in entries:
            try:
                is_dir = False
                is_symlink = False
                size = 0
                mod_time = 0
                
                try:
                    is_symlink = entry.is_symlink()
                except Exception:
                    pass

                try:
                    is_dir = entry.is_dir()
                except Exception:
                    pass

                if not is_dir and not is_symlink:
                    try:
                        if entry.is_file():
                            size = entry.stat().st_size
                    except Exception:
                        pass

                try:
                    mod_time = entry.stat().st_mtime
                except Exception:
                    pass

                try:
                    rel = guard.display(entry)
                except Exception:
                    rel = entry.name

                ext = entry.suffix.lower() if not is_dir else ""
                mime, _ = mimetypes.guess_type(entry.name)

                items.append({
                    "name": entry.name,
                    "path": rel,
                    "is_dir": is_dir,
                    "is_symlink": is_symlink,
                    "size": size,
                    "extension": ext,
                    "mime": mime or ("directory" if is_dir else "application/octet-stream"),
                    "mod_time": mod_time,
                })
            except Exception as item_err:
                logger.warning("Error leyendo item %s en workspace: %s", entry.name, item_err)
                continue

        try:
            disp_path = guard.display(resolved)
        except Exception:
            disp_path = "."

        return {"path": disp_path, "items": items}
    except Exception as top_err:
        logger.error("Error top-level en workspace_tree: %s", top_err, exc_info=True)
        return {"path": ".", "items": []}


@app.get("/workspace/file/content", dependencies=[Depends(require_api_key)])
async def workspace_file_content(path: str):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe.")
    size = resolved.stat().st_size
    if size > settings.max_file_bytes:
        raise HTTPException(status_code=400, detail="El archivo supera el tamaño máximo permitido.")
    
    mime, _ = mimetypes.guess_type(resolved.name)
    ext = resolved.suffix.lower()
    
    is_binary_ext = ext in {".docx", ".doc", ".pdf", ".zip", ".tar", ".gz", ".7z", ".xlsx", ".pptx", ".exe", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".mp3", ".wav"}
    
    if is_binary_ext:
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": f"[Documento/Archivo {resolved.name} ({size} bytes)]",
            "size": size,
            "extension": ext,
            "mime": mime or "application/octet-stream",
            "is_binary": True,
        }

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": content,
            "size": size,
            "extension": ext,
            "mime": mime or "text/plain",
            "is_binary": False,
        }
    except Exception as exc:
        return {
            "path": guard.display(resolved),
            "name": resolved.name,
            "content": f"[Error leyendo archivo: {exc}]",
            "size": size,
            "extension": ext,
            "mime": mime or "application/octet-stream",
            "is_binary": True,
        }


@app.get("/workspace/file/raw")
async def workspace_file_raw(path: str, token: str = ""):
    if not _valid_api_key(token):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe.")
    size = resolved.stat().st_size
    if size > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Archivo multimedia demasiado grande.")
    mime, _ = mimetypes.guess_type(resolved.name)
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=data, media_type=mime or "application/octet-stream")


@app.post("/workspace/file/create", dependencies=[Depends(require_api_key)])
async def workspace_file_create(req: WorkspaceCreateRequest):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.is_dir:
        resolved.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "message": f"Carpeta creada: {guard.display(resolved)}"}
    else:
        if resolved.exists():
            raise HTTPException(status_code=400, detail="El archivo ya existe.")
        content = req.content or ""
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"status": "ok", "message": f"Archivo creado: {guard.display(resolved)}"}


@app.delete("/workspace/file/delete", dependencies=[Depends(require_api_key)])
async def workspace_file_delete(path: str):
    guard = _get_workspace_guard()
    try:
        resolved = guard.resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="La ruta no existe.")
    try:
        if resolved.is_dir():
            resolved.rmdir()
        else:
            resolved.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo eliminar: {exc}")
    return {"status": "ok", "message": f"Eliminado: {guard.display(resolved)}"}


class TaskCreateRequest(BaseModel):
    title: str
    prompt: str
    next_run: float | None = None
    interval_seconds: float | None = None


@app.get("/tasks", dependencies=[Depends(require_api_key)])
async def list_tasks(include_disabled: bool = True):
    tasks = sessions.tasks.list(include_disabled=include_disabled)
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "kind": t.kind,
                "next_run": t.next_run,
                "interval_seconds": t.interval_seconds,
                "enabled": t.enabled,
                "created_at": t.created_at,
                "last_run": t.last_run,
            }
            for t in tasks
        ]
    }


@app.post("/tasks", dependencies=[Depends(require_api_key)])
async def create_task(req: TaskCreateRequest):
    import time
    next_run = req.next_run if req.next_run else (time.time() + 60)
    task_id = sessions.tasks.add(
        title=req.title,
        prompt=req.prompt,
        next_run=next_run,
        interval_seconds=req.interval_seconds,
    )
    return {"status": "ok", "task_id": task_id, "message": f"Tarea creada con ID {task_id}"}


@app.delete("/tasks/{task_id}", dependencies=[Depends(require_api_key)])
async def delete_task(task_id: int):
    success = sessions.tasks.cancel(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="La tarea no existe o ya fue cancelada.")
    return {"status": "ok", "message": f"Tarea {task_id} cancelada"}


@app.websocket("/ws/wake/{device_id}")
async def wakeword_endpoint(websocket: WebSocket, device_id: str) -> None:
    """Escucha permanente: recibe PCM crudo y avisa al oír «Hey JARVIS».

    Es el único canal siempre abierto, y por eso vive entero en el servidor: el
    audio de reposo no llega a ningún tercero ni consume presupuesto de OpenAI.
    Los ESP32 usan exactamente esta misma ruta que el HUD.
    """
    global _wake_streams

    if not _valid_api_key(websocket.query_params.get("token")):
        await websocket.close(code=1008, reason="Credenciales inválidas")
        return
    if not _SESSION_RE.fullmatch(device_id):
        await websocket.close(code=1008, reason="Identificador de dispositivo inválido")
        return
    if not (settings.voice_enabled and settings.wakeword_enabled):
        await websocket.close(code=1013, reason="Palabra de activación desactivada")
        return
    if _wake_streams >= settings.wakeword_max_streams:
        await websocket.close(code=1013, reason="Límite de escuchas alcanzado")
        return

    detector = WakeWordDetector(
        settings.wakeword_model,
        threshold=settings.wakeword_threshold,
        vad_threshold=settings.wakeword_vad_threshold,
        refractory_seconds=settings.wakeword_refractory_seconds,
    )
    await websocket.accept()
    _wake_streams += 1
    logger.info("Escucha de activación conectada: %s", device_id)
    try:
        await websocket.send_json(
            {
                "type": "wake_ready",
                "model": settings.wakeword_model,
                "sample_rate": WakeWordDetector.SAMPLE_RATE,
                "frame_samples": WakeWordDetector.FRAME_SAMPLES,
            }
        )
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                break
            # El cliente pide reiniciar al reanudar la escucha, para que el audio
            # anterior a una respuesta de JARVIS no dispare una activación tardía.
            if packet.get("text") is not None:
                if packet["text"].strip() == "reset":
                    detector.reset()
                continue
            chunk = packet.get("bytes")
            if not chunk:
                continue
            if len(chunk) > WakeWordDetector.MAX_BUFFER_BYTES:
                await websocket.close(code=1009, reason="Fragmento de audio excesivo")
                return
            try:
                score = await asyncio.to_thread(detector.process, chunk)
            except LocalVoiceError as exc:
                logger.error("Detector de activación no disponible: %s", exc)
                await websocket.send_json({"type": "error", "error": str(exc)})
                await websocket.close(code=1011, reason="Detector no disponible")
                return
            if score is not None:
                logger.info("Activación detectada en %s (%.2f)", device_id, score)
                await websocket.send_json(
                    {"type": "wake", "score": round(score, 4), "device": device_id}
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error en la escucha de activación %s", device_id)
        # Sin un cierre explícito el cliente se queda esperando audio que nunca
        # llegará, en vez de reintentar la conexión.
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="Error interno de escucha")
    finally:
        _wake_streams -= 1
        logger.info("Escucha de activación cerrada: %s", device_id)


@app.websocket("/ws/voice/{session_id}")
async def realtime_voice_endpoint(websocket: WebSocket, session_id: str) -> None:
    """Conversación por voz con OpenAI Realtime, alojada en el servidor.

    El dispositivo solo captura y reproduce audio PCM de 24 kHz. Todo lo demás
    —herramientas, aprobaciones, objetivos, conectores— sigue ocurriendo aquí,
    igual que en el canal de texto. La sesión se abre después de «Hey JARVIS»,
    nunca en reposo: es lo que mantiene el gasto acotado.
    """
    global _realtime_sessions

    if not _valid_api_key(websocket.query_params.get("token")):
        await websocket.close(code=1008, reason="Credenciales inválidas")
        return
    if not _SESSION_RE.fullmatch(session_id):
        await websocket.close(code=1008, reason="Identificador de sesión inválido")
        return
    if not settings.realtime_conversation_enabled:
        await websocket.close(code=1013, reason="Conversación Realtime desactivada")
        return
    if not realtime_voice.budget_available():
        await websocket.close(code=1013, reason="Presupuesto diario agotado")
        return
    if _realtime_sessions >= settings.realtime_max_sessions:
        await websocket.close(code=1013, reason="Límite de conversaciones alcanzado")
        return

    emotion = EmotionState()
    pending_approvals: dict[str, asyncio.Future[bool]] = {}

    async def on_audio(pcm: bytes) -> None:
        await websocket.send_bytes(pcm)

    async def on_event(payload: dict[str, object]) -> None:
        # La emoción la elige JARVIS con set_emotion; el HUD la pinta.
        if payload.get("type") == "tool" and payload.get("phase") == "completed":
            await websocket.send_json({"type": "emotion", "emotion": emotion.current})
        if payload.get("type") == "tool":
            payload = dict(payload)
            arguments = payload.get("arguments")
            if isinstance(arguments, dict):
                payload["arguments"] = _public_approval_arguments(arguments)
            # El resultado crudo nunca sale al cliente; solo se usa aquí para
            # traducirlo en órdenes del reproductor.
            result = payload.pop("_result", None)
            music = _music_command(str(payload.get("tool", "")), result)
            if music is not None:
                payload["music"] = music
        await websocket.send_json(payload)

    async def confirm(name: str, arguments: dict[str, object]) -> bool:
        approval_id = secrets.token_urlsafe(8)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending_approvals[approval_id] = future
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
        try:
            approved = await asyncio.wait_for(
                future, timeout=settings.approval_timeout_seconds
            )
            reason = "usuario"
        except asyncio.TimeoutError:
            approved, reason = False, "timeout"
        finally:
            pending_approvals.pop(approval_id, None)
        await websocket.send_json(
            {
                "type": "approval_resolved",
                "approval_id": approval_id,
                "approved": approved,
                "reason": reason,
            }
        )
        return approved

    budget_exceeded = asyncio.Event()

    async def on_usage(usage: dict[str, int]) -> None:
        budget = settings.realtime_daily_budget_usd
        if not budget:
            return
        # Lo ya guardado hoy más lo que lleva esta conversación, que todavía no
        # se ha escrito: sin sumarlo, la sesión en curso sería invisible.
        spent = realtime_voice.spend_today() + realtime_voice.estimate_cost(usage)
        if spent < budget:
            return
        # El corte queda en el registro del servidor, no en la pantalla: el
        # cliente solo ve que la conversación termina y vuelve a la voz local.
        logger.warning(
            "Techo de gasto Realtime alcanzado: %.4f USD de %.2f", spent, budget
        )
        budget_exceeded.set()

    conversation = RealtimeConversation(
        settings,
        sessions.build_registry(emotion),
        on_audio=on_audio,
        on_event=on_event,
        confirm=confirm,
        on_usage=on_usage,
    )
    await websocket.accept()
    try:
        await conversation.connect()
    except Exception as exc:
        logger.exception("No pude abrir la conversación Realtime")
        await websocket.send_json({"type": "error", "error": str(exc)})
        await websocket.close(code=1011, reason="Realtime no disponible")
        return

    _realtime_sessions += 1
    realtime_voice.record_session()
    logger.info("Conversación Realtime abierta: %s", session_id)
    pump = asyncio.create_task(conversation.pump())
    try:
        await websocket.send_json(
            {
                "type": "voice_ready",
                "sample_rate": realtime_module.SAMPLE_RATE,
                "model": settings.openai_realtime_model,
            }
        )
        while True:
            # Un solo bucle de recepción reparte el canal: binario es audio y texto
            # son órdenes. Leer aprobaciones aparte robaría fragmentos de audio.
            packet = await asyncio.wait_for(
                websocket.receive(), timeout=settings.realtime_idle_seconds
            )
            if (
                packet["type"] == "websocket.disconnect"
                or pump.done()
                or budget_exceeded.is_set()
            ):
                break
            if packet.get("text") is not None:
                await _handle_voice_command(
                    packet["text"], conversation, pending_approvals
                )
                continue
            chunk = packet.get("bytes")
            if chunk:
                await conversation.send_audio(chunk)
    except asyncio.TimeoutError:
        # Silencio prolongado: cerrar libera la sesión y detiene cualquier gasto.
        logger.info("Conversación Realtime inactiva: %s", session_id)
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "voice_idle_timeout"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error en la conversación Realtime %s", session_id)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        await conversation.close()
        _realtime_sessions -= 1
        realtime_voice.record_usage(conversation.usage)
        logger.info(
            "Conversación Realtime cerrada: %s (%s)", session_id, conversation.usage
        )
        with contextlib.suppress(Exception):
            await websocket.close()


async def _handle_voice_command(
    raw: str,
    conversation: RealtimeConversation,
    pending_approvals: dict[str, asyncio.Future[bool]],
) -> None:
    """Órdenes de texto que el dispositivo intercala en el canal de audio."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    if payload.get("type") == "cancel":
        await conversation.cancel_response()
        return
    if payload.get("type") == "approval" and isinstance(payload.get("approved"), bool):
        future = pending_approvals.get(str(payload.get("approval_id")))
        if future is not None and not future.done():
            future.set_result(payload["approved"])


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
                    music = _music_command(name, result)
                    if music is not None:
                        # La orden va al reproductor del HUD; el texto crudo de la
                        # herramienta no aporta nada al usuario.
                        payload.pop("output", None)
                        payload["music"] = music
                await websocket.send_json(payload)

            # El canal continúa disponible tras un fallo del proveedor.
            try:
                reply = await orchestrator.send(
                    message,
                    on_text_delta=send_text_delta,
                    confirm=request_confirmation,
                    on_tool_event=send_tool_event,
                )
            except WebSocketDisconnect:
                # El cliente puede recargar o cerrar el HUD mientras el proveedor
                # aún genera. Deja que el manejador exterior cierre la sesión sin
                # intentar escribir un segundo mensaje sobre el socket cerrado.
                raise
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

"""Conversación OpenAI Realtime alojada en el gateway.

La sesión vive **en el servidor**, no en el navegador. Es lo que permite que
Realtime no sea solo una voz bonita sino el cerebro que actúa: las herramientas,
las aprobaciones, los objetivos y los conectores ya viven aquí, y un ESP32 —que
no puede montar WebRTC— habla por el mismo WebSocket que el HUD.

El audio de reposo nunca llega hasta aquí: la palabra de activación se detecta
antes, en local, y solo entonces se abre esta sesión. Eso es lo que mantiene el
coste en el orden de unos pocos euros al mes en vez de cientos.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis_core.config import Settings
from jarvis_core.tools.base import ToolRegistry, ToolResult

logger = logging.getLogger("jarvis.realtime")

#: Formato nativo de Realtime en ambos sentidos.
AUDIO_FORMAT: dict[str, Any] = {"type": "audio/pcm", "rate": 24000}
SAMPLE_RATE = 24000

AudioSink = Callable[[bytes], Awaitable[None]]
EventSink = Callable[[dict[str, Any]], Awaitable[None]]
Confirmer = Callable[[str, dict[str, Any]], Awaitable[bool]]


class RealtimeSessionError(RuntimeError):
    pass


class RealtimeConversation:
    """Puente entre un dispositivo de JARVIS y una sesión Realtime de OpenAI."""

    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        *,
        on_audio: AudioSink,
        on_event: EventSink,
        confirm: Confirmer | None = None,
        on_usage: Callable[[dict[str, int]], Awaitable[None]] | None = None,
        client: Any = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self._on_audio = on_audio
        self._on_event = on_event
        self._confirm = confirm
        self._on_usage = on_usage
        self._client = client
        self._connection: Any = None
        self._manager: Any = None
        # Las herramientas se ejecutan fuera del bucle de eventos: una búsqueda web
        # lenta no debe congelar el audio que ya está sonando.
        self._tool_tasks: set[asyncio.Task[None]] = set()
        self.usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "input_audio_tokens": 0,
            "output_audio_tokens": 0,
            "cached_tokens": 0,
        }

    # ── Configuración de la sesión ───────────────────────────────────────────

    def _tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": definition["name"],
                "description": definition["description"],
                "parameters": definition["input_schema"],
            }
            for definition in self.registry.definitions()
        ]

    def _instructions(self) -> str:
        # Se parte de la misma personalidad que el resto del sistema para que
        # JARVIS no suene como dos agentes distintos según el canal.
        return self.settings.system_prompt() + (
            "\n\nEstás hablando por voz. Responde en frases cortas y naturales, "
            "sin listas, sin markdown y sin leer URLs completas. Antes de usar una "
            "herramienta que tarde (buscar en internet, consultar un conector), di "
            "una frase breve para que el usuario sepa que sigues ahí."
        )

    def session_config(self) -> dict[str, Any]:
        turn_detection: dict[str, Any] = {
            "type": "server_vad",
            "threshold": self.settings.realtime_vad_threshold,
            "silence_duration_ms": self.settings.realtime_silence_ms,
            "prefix_padding_ms": 300,
            "create_response": True,
            "interrupt_response": True,
        }
        return {
            "type": "realtime",
            "model": self.settings.openai_realtime_model,
            "instructions": self._instructions(),
            "output_modalities": ["audio"],
            "max_output_tokens": self.settings.openai_realtime_max_output_tokens,
            "tools": self._tools(),
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": AUDIO_FORMAT,
                    "turn_detection": turn_detection,
                    # La transcripción del usuario alimenta el registro y la
                    # memoria; no añade coste de audio porque ya se envió.
                    "transcription": {"model": "whisper-1"},
                },
                "output": {
                    "format": AUDIO_FORMAT,
                    "voice": self.settings.openai_realtime_voice,
                    "speed": self.settings.realtime_speed,
                },
            },
        }

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if not self.settings.openai_responses_api_key:
            raise RealtimeSessionError("Falta OPENAI_API_KEY para la voz Realtime.")
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self.settings.openai_responses_api_key)
        self._manager = self._client.realtime.connect(
            model=self.settings.openai_realtime_model
        )
        self._connection = await self._manager.__aenter__()
        await self._connection.session.update(session=self.session_config())
        logger.info("Sesión Realtime abierta (%s)", self.settings.openai_realtime_model)

    async def close(self) -> None:
        for task in list(self._tool_tasks):
            task.cancel()
        self._tool_tasks.clear()
        if self._manager is not None:
            try:
                await self._manager.__aexit__(None, None, None)
            except Exception:
                logger.debug("Cierre de la sesión Realtime con error", exc_info=True)
        self._manager = None
        self._connection = None

    # ── Entrada de audio ─────────────────────────────────────────────────────

    async def send_audio(self, pcm: bytes) -> None:
        """Empuja audio del dispositivo. El VAD del servidor decide los turnos."""
        if self._connection is None:
            return
        await self._connection.input_audio_buffer.append(
            audio=base64.b64encode(pcm).decode("ascii")
        )

    async def cancel_response(self) -> None:
        """Corta lo que JARVIS esté diciendo (el usuario le interrumpió)."""
        if self._connection is None:
            return
        try:
            await self._connection.response.cancel()
        except Exception:
            logger.debug("No había respuesta que cancelar", exc_info=True)

    # ── Bucle de eventos ─────────────────────────────────────────────────────

    async def pump(self) -> None:
        """Lee eventos de OpenAI hasta que la conexión se cierre."""
        if self._connection is None:
            raise RealtimeSessionError("La sesión Realtime no está conectada.")
        async for event in self._connection:
            await self._handle(event)

    async def _handle(self, event: Any) -> None:
        kind = getattr(event, "type", "")

        if kind == "response.output_audio.delta":
            await self._on_audio(base64.b64decode(event.delta))
        elif kind == "response.output_audio_transcript.delta":
            await self._on_event({"type": "reply_delta", "delta": event.delta})
        elif kind == "response.output_audio_transcript.done":
            await self._on_event({"type": "reply_done", "text": event.transcript})
        elif kind == "input_audio_buffer.speech_started":
            await self._on_event({"type": "state", "state": "listening"})
        elif kind == "input_audio_buffer.speech_stopped":
            await self._on_event({"type": "state", "state": "thinking"})
        elif kind == "conversation.item.input_audio_transcription.completed":
            await self._on_event({"type": "transcript", "text": event.transcript})
        elif kind == "response.function_call_arguments.done":
            self._spawn_tool(event.call_id, event.name, event.arguments)
        elif kind == "response.done":
            self._record_usage(event)
            await self._on_event({"type": "state", "state": "idle"})
            # Cada respuesta es el único punto donde el gasto se conoce de verdad.
            # Comprobarlo solo al abrir la sesión dejaría que una conversación
            # larga se saltara el techo entera.
            if self._on_usage is not None:
                await self._on_usage(dict(self.usage))
        elif kind == "error":
            message = getattr(getattr(event, "error", None), "message", "desconocido")
            logger.error("Error de Realtime: %s", message)
            await self._on_event({"type": "error", "error": message})

    def _record_usage(self, event: Any) -> None:
        usage = getattr(getattr(event, "response", None), "usage", None)
        if usage is None:
            return
        self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
        details = getattr(usage, "input_token_details", None)
        self.usage["input_audio_tokens"] += getattr(details, "audio_tokens", 0) or 0
        self.usage["cached_tokens"] += getattr(details, "cached_tokens", 0) or 0
        out_details = getattr(usage, "output_token_details", None)
        self.usage["output_audio_tokens"] += getattr(out_details, "audio_tokens", 0) or 0

    # ── Herramientas ─────────────────────────────────────────────────────────

    def _spawn_tool(self, call_id: str, name: str, raw_arguments: str) -> None:
        task = asyncio.create_task(self._run_tool(call_id, name, raw_arguments))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    async def _run_tool(self, call_id: str, name: str, raw_arguments: str) -> None:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            await self._reply_tool(
                call_id, ToolResult(f"Argumentos inválidos: {exc}", is_error=True)
            )
            return
        if not isinstance(arguments, dict):
            await self._reply_tool(
                call_id,
                ToolResult("Argumentos inválidos: se esperaba un objeto.", is_error=True),
            )
            return

        tool = self.registry.get(name)
        if tool is None:
            await self._reply_tool(
                call_id, ToolResult(f"Herramienta desconocida: '{name}'", is_error=True)
            )
            return

        await self._on_event(
            {"type": "tool", "phase": "proposed", "tool": name, "arguments": arguments}
        )

        # Las acciones sensibles se aprueban igual que por texto: la decisión es
        # del usuario y nunca del modelo, venga por el canal que venga.
        if tool.requires_confirmation:
            approved = False
            if self._confirm is not None:
                try:
                    approved = await self._confirm(name, arguments)
                except Exception:
                    logger.exception("Fallo pidiendo aprobación de %s", name)
            if not approved:
                await self._on_event(
                    {"type": "tool", "phase": "denied", "tool": name}
                )
                await self._reply_tool(
                    call_id,
                    ToolResult("El usuario denegó la acción.", is_error=True),
                )
                return

        await self._on_event({"type": "tool", "phase": "running", "tool": name})
        result = await self.registry.execute(name, arguments)
        await self._on_event(
            {
                "type": "tool",
                "phase": "completed",
                "tool": name,
                "is_error": result.is_error,
            }
        )
        await self._reply_tool(call_id, result)

    async def _reply_tool(self, call_id: str, result: ToolResult) -> None:
        """Devuelve el resultado y pide a JARVIS que siga hablando."""
        if self._connection is None:
            return
        try:
            await self._connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result.content[: self.settings.realtime_max_tool_output],
                }
            )
            await self._connection.response.create()
        except Exception:
            logger.exception("No pude devolver el resultado de la herramienta")

"""Orquestador: el bucle de agente de JARVIS.

Implementa el bucle de uso de herramientas contra un `LLMProvider`:

    1. Llamar al modelo con el historial y las herramientas.
    2. Si el modelo pide herramientas, ejecutarlas y devolver los resultados.
    3. Repetir hasta que el modelo dé una respuesta final (o se alcance el límite).

Mantiene el historial de la conversación (memoria de corto plazo) en el propio objeto.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.config import Settings
from jarvis_core.memory.store import MemoryStore
from jarvis_core.llm.base import LLMProvider, TextDeltaFn
from jarvis_core.tools.base import ToolRegistry, ToolResult
from jarvis_core.tools.clipping import clip_structured

logger = logging.getLogger("jarvis.orchestrator")

# Callback opcional para pedir confirmación antes de ejecutar herramientas sensibles.
# Recibe (nombre_herramienta, argumentos) y devuelve True para permitir.
ConfirmFn = Callable[[str, dict[str, Any]], Awaitable[bool]]
ToolEventFn = Callable[[str, str, dict[str, Any], ToolResult | None], Awaitable[None]]


@dataclass
class AgentReply:
    """Respuesta final de un turno del agente."""

    text: str
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "end_turn"
    emotion: str = "neutral"


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        settings: Settings,
        confirm: ConfirmFn | None = None,
        emotion: EmotionState | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._confirm = confirm
        self._emotion = emotion
        self._memory = memory
        self._memory_facts = settings.memory_facts_in_prompt
        self._system = settings.system_prompt()
        self._persona_overlay = settings.persona_extra.strip()
        self._history: list[dict[str, Any]] = []
        self._send_lock = asyncio.Lock()
        try:
            self._timezone = ZoneInfo(settings.timezone) if settings.timezone else None
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Zona horaria inválida: %s", settings.timezone)
            self._timezone = None

    def set_registry(self, registry: ToolRegistry) -> None:
        """Cambia las herramientas disponibles conservando la conversación.

        Se usa al registrar o quitar un conector: las herramientas que dependen
        de él aparecen o desaparecen sin obligar al usuario a empezar de cero.
        """
        self._registry = registry

    def set_persona(self, overlay: str) -> None:
        """Ajusta tono y reglas de la casa **sin reiniciar** el gateway.

        El prompt base se congela al construir el orquestador, y hasta ahora era
        la única vía: cambiar la personalidad exigía reiniciar. Esto va por la
        capa volátil, así que tampoco invalida el prefijo cacheado.
        """
        self._persona_overlay = overlay.strip()

    @property
    def persona(self) -> str:
        return self._persona_overlay

    def _remembered_facts(self, user_message: str) -> list[str]:
        """Hechos de la memoria que merece la pena tener delante este turno.

        Hasta ahora la memoria solo existía como herramienta: JARVIS recordaba
        algo únicamente si *decidía* llamar a `recall`, así que lo normal era que
        no recordara nada. Aquí se le ponen delante sin que tenga que pedirlos.

        La selección es por coincidencia de texto y por recencia, no por
        significado: para eso hacen falta embeddings, que es un paso aparte.
        """
        if self._memory is None:
            return []
        vistos: dict[str, str] = {}
        try:
            relevantes = self._memory.recall(user_message, limit=self._memory_facts)
            recientes = self._memory.recall("", limit=self._memory_facts)
        except Exception:
            logger.warning("No pude leer la memoria para este turno", exc_info=True)
            return []
        for hecho in [*relevantes, *recientes]:
            if hecho.key not in vistos:
                vistos[hecho.key] = hecho.value
        lineas = [f"- {clave}: {valor}" for clave, valor in vistos.items()]
        return lineas[: self._memory_facts]

    def _build_overlay(self, user_message: str) -> str:
        """Lo que cambia entre turnos, separado de lo que no."""
        bloques: list[str] = []
        ahora = datetime.now(self._timezone)
        bloques.append(f"Fecha y hora actuales: {ahora.strftime('%A %d de %B de %Y, %H:%M')}.")
        if self._persona_overlay:
            bloques.append(f"Reglas de la casa vigentes:\n{self._persona_overlay}")
        hechos = self._remembered_facts(user_message)
        if hechos:
            bloques.append(
                "Lo que ya sabes de tu jefe (memoria a largo plazo; úsalo sin "
                "anunciar que lo recuerdas, y no vuelvas a preguntar lo que ya "
                "está aquí):\n" + "\n".join(hechos)
            )
        return "\n\n".join(bloques)

    def reset(self) -> None:
        """Olvida la conversación actual (no la memoria a largo plazo)."""
        self._history = []

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._history

    async def send(
        self,
        user_message: str,
        on_text_delta: TextDeltaFn | None = None,
        confirm: ConfirmFn | None = None,
        on_tool_event: ToolEventFn | None = None,
    ) -> AgentReply:
        """Serializa los turnos de una sesión para no corromper su historial."""
        async with self._send_lock:
            return await self._send_locked(
                user_message, on_text_delta, confirm, on_tool_event
            )

    async def _send_locked(
        self,
        user_message: str,
        on_text_delta: TextDeltaFn | None = None,
        confirm: ConfirmFn | None = None,
        on_tool_event: ToolEventFn | None = None,
    ) -> AgentReply:
        """Procesa un mensaje del usuario y devuelve la respuesta final del agente."""
        self._history.append({"role": "user", "content": user_message})
        tools = self._registry.definitions()
        tools_used: list[str] = []
        overlay = self._build_overlay(user_message)

        for _ in range(self._settings.max_tool_iterations):
            try:
                # Sin tope, un proveedor que no responde cuelga el turno entero y
                # con él al cliente: el gateway solo manda su mensaje final
                # cuando este bucle retorna, así que el HUD se quedaba bloqueado
                # —sin teclado, sin cerrar la conversación y sin voz, porque la
                # síntesis también sale al cerrar— y sin forma de recuperarse.
                # Rendirse con un mensaje es peor que responder, pero es
                # infinitamente mejor que no volver nunca.
                response = await asyncio.wait_for(
                    self._llm.complete(
                        system=self._system,
                        history=self._history,
                        tools=tools,
                        on_text_delta=on_text_delta,
                        system_overlay=overlay,
                    ),
                    timeout=self._settings.llm_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "El proveedor no respondió en %.0f s; se cierra el turno.",
                    self._settings.llm_timeout_seconds,
                )
                self._trim_history()
                return AgentReply(
                    text=(
                        "El modelo no respondió a tiempo. Vuelve a intentarlo; si "
                        "se repite, sube JARVIS_LLM_TIMEOUT o revisa el proveedor."
                    ),
                    tools_used=tools_used,
                    stop_reason="timeout",
                    emotion=self._emotion.current if self._emotion else "concern",
                )
            # Añadir el turno del asistente al historial neutral. Se guarda el contenido
            # nativo (`raw`) para poder reutilizarlo si se sigue con el mismo proveedor.
            self._history.append(
                {
                    "role": "assistant",
                    "text": response.text,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "input": tc.input}
                        for tc in response.tool_calls
                    ],
                    "provider": response.provider,
                    "raw": response.assistant_content,
                }
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                self._trim_history()
                return AgentReply(
                    text=response.text,
                    tools_used=tools_used,
                    stop_reason=response.stop_reason,
                    emotion=self._emotion.current if self._emotion else "neutral",
                )

            # Ejecutar todas las herramientas pedidas y devolver los resultados juntos.
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                tools_used.append(call.name)
                # Los argumentos pueden contener correos, mensajes o secretos operativos.
                logger.info("Ejecutando herramienta %s", call.name)
                if on_tool_event is not None:
                    await on_tool_event("proposed", call.name, call.input, None)

                allowed = await self._maybe_confirm(call.name, call.input, confirm)
                if not allowed:
                    if on_tool_event is not None:
                        await on_tool_event("denied", call.name, call.input, None)
                    results.append(
                        {
                            "id": call.id,
                            "name": call.name,
                            "content": "El usuario ha denegado la ejecución de esta herramienta.",
                            "is_error": True,
                        }
                    )
                    continue

                if on_tool_event is not None:
                    await on_tool_event("running", call.name, call.input, None)
                result = await self._registry.execute(call.name, call.input)
                if on_tool_event is not None:
                    # El cliente recibe el resultado íntegro: tiene su propio
                    # recorte y sabe presentarlo. Al modelo se le da menos.
                    await on_tool_event("completed", call.name, call.input, result)
                # Un inventario de casa entero no solo ocupa esta petición: se
                # queda en el historial y vuelve a viajar en cada vuelta del
                # bucle, así que cada iteración salía más lenta que la anterior.
                # El recorte respeta la estructura, de modo que lo que lee el
                # modelo sigue siendo JSON válido con menos elementos.
                content, clipped = clip_structured(
                    result.content, self._settings.max_tool_output_chars
                )
                if clipped is not None:
                    # Que el modelo sepa que hay más: si no, afirma que eso es
                    # todo lo que existe en vez de ofrecerse a filtrar.
                    content += (
                        f"\n\n[Resultado recortado: se muestran {clipped['shown']} de "
                        f"{clipped['total']}. Vuelve a llamar con un filtro para ver el resto.]"
                    )
                results.append(
                    {
                        "id": call.id,
                        "name": call.name,
                        "content": content,
                        "is_error": result.is_error,
                    }
                )

            self._history.append({"role": "tool", "results": results})

        # Si llegamos aquí, se agotó el número de iteraciones.
        self._trim_history()
        return AgentReply(
            text="(He alcanzado el límite de pasos de herramientas sin terminar la tarea.)",
            tools_used=tools_used,
            stop_reason="max_iterations",
            emotion=self._emotion.current if self._emotion else "neutral",
        )

    def _trim_history(self) -> None:
        """Limita el contexto conservando turnos completos desde un mensaje de usuario."""
        limit = self._settings.max_history_items
        if len(self._history) <= limit:
            return
        trimmed = self._history[-limit:]
        while trimmed and trimmed[0].get("role") != "user":
            trimmed.pop(0)
        self._history = trimmed

    async def _maybe_confirm(
        self,
        name: str,
        arguments: dict[str, Any],
        confirm: ConfirmFn | None = None,
    ) -> bool:
        tool = self._registry.get(name)
        if tool is None or not tool.requires_confirmation:
            return True
        confirmation_policy = confirm or self._confirm
        if confirmation_policy is None:
            # Seguridad fail-closed: una herramienta sensible nunca se ejecuta si el
            # canal no proporcionó una política explícita de confirmación.
            logger.warning(
                "Herramienta sensible %s denegada: no hay política de confirmación",
                name,
            )
            return False
        return await confirmation_policy(name, arguments)

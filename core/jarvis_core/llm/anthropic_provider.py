"""Proveedor de LLM basado en la API de Anthropic (Claude).

Cerebro recomendado de JARVIS. Usa el SDK oficial con *adaptive thinking* y el parámetro
`effort`. Traduce el historial neutral del orquestador al formato de bloques de Anthropic.
"""

from __future__ import annotations

from typing import Any

from jarvis_core.llm.base import LLMProvider, LLMResponse, TextDeltaFn, ToolCall

NAME = "anthropic"

#: Modelos que aceptan `{"role": "system"}` dentro de `messages`. Sonnet 5 no
#: está: enviárselo es un error de petición, no una degradación silenciosa.
_MODELS_WITH_SYSTEM_MESSAGES = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-mythos-5",
)


class AnthropicProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        effort: str = "high",
    ) -> None:
        from anthropic import AsyncAnthropic

        if not api_key:
            raise ValueError(
                "Falta ANTHROPIC_API_KEY. Ponla en tu entorno o en el fichero .env."
            )
        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    def _supports_system_messages(self) -> bool:
        """¿Admite este modelo mensajes de sistema a mitad de conversación?

        Es la familia Opus 5 / 4.8 y Fable/Mythos 5. Sonnet 5 **no**, y mandarle
        uno sería un error de petición, así que ahí la capa volátil se suma al
        prompt aunque cueste la caché.
        """
        model = self.model.lower()
        return any(
            model.startswith(prefijo)
            for prefijo in _MODELS_WITH_SYSTEM_MESSAGES
        )

    def _to_messages(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Traduce el historial neutral a mensajes de la API de Anthropic."""
        messages: list[dict[str, Any]] = []
        for item in history:
            role = item["role"]
            if role == "user":
                messages.append({"role": "user", "content": item["content"]})
            elif role == "assistant":
                # Reutiliza el contenido nativo si lo generó este mismo proveedor
                # (preserva bloques de razonamiento y de uso de herramientas).
                if item.get("provider") == NAME and item.get("raw") is not None:
                    messages.append({"role": "assistant", "content": item["raw"]})
                else:
                    content: list[dict[str, Any]] = []
                    if item.get("text"):
                        content.append({"type": "text", "text": item["text"]})
                    for tc in item.get("tool_calls", []):
                        content.append(
                            {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": tc["name"],
                                "input": tc["input"],
                            }
                        )
                    messages.append({"role": "assistant", "content": content or item.get("text", "")})
            elif role == "tool":
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": r["id"],
                        "content": r["content"],
                        "is_error": r.get("is_error", False),
                    }
                    for r in item["results"]
                ]
                messages.append({"role": "user", "content": blocks})
        return messages

    async def complete(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaFn | None = None,
        system_overlay: str = "",
    ) -> LLMResponse:
        messages = self._to_messages(history)
        # Las instrucciones que cambian cada turno viajan como mensaje de sistema
        # a mitad de conversación, no dentro de `system`. Es el canal previsto
        # para esto y, sobre todo, deja intacto el prefijo cacheado: metidas
        # arriba invalidarían la caché en cada turno y el ahorro sería cero.
        # Debe ir tras un mensaje de usuario y ser la última entrada.
        if system_overlay and self._supports_system_messages() and messages:
            if messages[-1].get("role") == "user":
                messages = [*messages, {"role": "system", "content": system_overlay}]
            else:
                system = f"{system}\n\n{system_overlay}"
        elif system_overlay:
            system = f"{system}\n\n{system_overlay}"

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # Un solo punto de caché al final del bloque estable. El orden de
            # render es tools → system → messages, así que cubre las dos cosas
            # que se repiten palabra por palabra en cada vuelta del bucle de
            # herramientas: las definiciones y este prompt.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if tools:
            kwargs["tools"] = tools

        # En streaming de verdad, no simulado. Antes se pedía la respuesta
        # entera y se entregaba de una vez al canal: el HUD la pintaba de golpe
        # en vez de en karaoke, la voz no podía empezar hasta tenerla completa,
        # y con max_tokens alto una respuesta larga podía agotar el tiempo de la
        # petición HTTP.
        async with self._client.messages.stream(**kwargs) as stream:
            if on_text_delta is not None:
                async for chunk in stream.text_stream:
                    await on_text_delta(chunk)
            response = await stream.get_final_message()

        if response.stop_reason == "refusal":
            return LLMResponse(
                text="(He tenido que declinar esta petición por motivos de seguridad.)",
                stop_reason="refusal",
                provider=NAME,
                assistant_content=response.content,
            )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )

        usage = response.usage
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            provider=NAME,
            assistant_content=response.content,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                # Si esto sale cero llamada tras llamada, algo está cambiando el
                # prefijo y se está pagando entero cada vez.
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            },
        )

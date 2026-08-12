"""Proveedor de LLM basado en la API de Anthropic (Claude).

Cerebro recomendado de JARVIS. Usa el SDK oficial con *adaptive thinking* y el parámetro
`effort`. Traduce el historial neutral del orquestador al formato de bloques de Anthropic.
"""

from __future__ import annotations

from typing import Any

from jarvis_core.llm.base import LLMProvider, LLMResponse, ToolCall

NAME = "anthropic"


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
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": self._to_messages(history),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

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

        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            provider=NAME,
            assistant_content=response.content,
            usage={
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            },
        )

"""Proveedor compatible con la API de OpenAI.

Sirve para cualquier servicio que exponga la API estilo OpenAI (chat.completions):
  - NVIDIA NIM  (https://integrate.api.nvidia.com/v1)
  - Ollama      (http://localhost:11434/v1)  → modelos locales, privacidad/offline
  - Otros endpoints compatibles

Traduce el historial neutral de JARVIS al formato de mensajes y herramientas de OpenAI.
La memoria interna de JARVIS es la misma sea cual sea el proveedor.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis_core.llm.base import LLMProvider, LLMResponse, TextDeltaFn, ToolCall

NAME = "openai"


class OpenAICompatibleProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int = 4096,
    ) -> None:
        from openai import AsyncOpenAI

        if not model:
            raise ValueError("Falta el nombre del modelo (JARVIS_OPENAI_MODEL).")
        # Algunos endpoints locales (Ollama) no exigen api_key; se usa un placeholder.
        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url or None)
        self.model = model
        self.max_tokens = max_tokens

    def _to_messages(self, system: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for item in history:
            role = item["role"]
            if role == "user":
                messages.append({"role": "user", "content": item["content"]})
            elif role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": item.get("text") or None}
                tool_calls = item.get("tool_calls", [])
                if tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["input"], ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                messages.append(msg)
            elif role == "tool":
                for r in item["results"]:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": r["id"],
                            "content": r["content"],
                        }
                    )
        return messages

    @staticmethod
    def _to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    async def complete(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaFn | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_messages(system, history),
        }
        if tools:
            kwargs["tools"] = self._to_tools(tools)

        if on_text_delta is not None:
            return await self._complete_streaming(kwargs, on_text_delta)

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, ValueError):
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=arguments))

        stop_reason = "tool_use" if tool_calls else "end_turn"

        usage = {}
        if response.usage is not None:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        return LLMResponse(
            text=(message.content or "").strip(),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            provider=NAME,
            assistant_content=None,  # se reconstruye desde text/tool_calls
            usage=usage,
        )

    async def _complete_streaming(
        self,
        kwargs: dict[str, Any],
        on_text_delta: TextDeltaFn,
    ) -> LLMResponse:
        """Reconstruye texto y tool calls desde el stream compatible con OpenAI."""
        stream = await self._client.chat.completions.create(**kwargs, stream=True)
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        finish_reason = "end_turn"
        usage: dict[str, int] = {}

        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = {
                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0),
                }
            if not getattr(chunk, "choices", None):
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                await on_text_delta(content)

            for tool_delta in getattr(delta, "tool_calls", None) or []:
                index = tool_delta.index
                current = tool_parts.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if tool_delta.id:
                    current["id"] += tool_delta.id
                function = tool_delta.function
                if function is not None:
                    if function.name:
                        current["name"] += function.name
                    if function.arguments:
                        current["arguments"] += function.arguments

        tool_calls: list[ToolCall] = []
        for index, raw in sorted(tool_parts.items()):
            try:
                arguments = json.loads(raw["arguments"] or "{}")
            except (json.JSONDecodeError, ValueError):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=raw["id"] or f"tool-{index}",
                    name=raw["name"],
                    input=arguments,
                )
            )

        return LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else finish_reason,
            provider=NAME,
            assistant_content=None,
            usage=usage,
        )

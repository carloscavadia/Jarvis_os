"""Proveedor nativo de OpenAI basado en Responses API.

La voz permanece local: este proveedor recibe solamente texto después de que el HUB
detecta la palabra de activación y termina la transcripción. ``store=False`` evita
conservar las respuestas en OpenAI y el historial se limita en el orquestador.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_core.llm.base import LLMProvider, LLMResponse, TextDeltaFn, ToolCall

NAME = "openai_responses"


class OpenAIResponsesProvider(LLMProvider):
    name = NAME

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-nano",
        max_tokens: int = 900,
        reasoning_effort: str = "low",
        verbosity: str = "low",
        daily_token_limit: int = 100_000,
        usage_db_path: str = "data/openai_usage.db",
        history_items: int = 24,
    ) -> None:
        from openai import AsyncOpenAI

        if not api_key:
            raise ValueError(
                "Falta OPENAI_API_KEY. Ponla únicamente en el fichero .env."
            )
        if not model:
            raise ValueError("Falta JARVIS_OPENAI_RESPONSES_MODEL.")
        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max(64, max_tokens)
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.daily_token_limit = max(0, daily_token_limit)
        self.usage_db_path = usage_db_path
        self.history_items = max(4, history_items)
        self._prepare_usage_db()

    def _bounded_history(
        self, history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if len(history) <= self.history_items:
            return history
        bounded = history[-self.history_items :]
        while bounded and bounded[0].get("role") != "user":
            bounded = bounded[1:]
        return bounded

    def _prepare_usage_db(self) -> None:
        path = Path(self.usage_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as database:
            database.execute(
                """CREATE TABLE IF NOT EXISTS daily_usage (
                       day TEXT PRIMARY KEY,
                       input_tokens INTEGER NOT NULL DEFAULT 0,
                       output_tokens INTEGER NOT NULL DEFAULT 0
                   )"""
            )

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _tokens_used_today(self) -> int:
        with sqlite3.connect(self.usage_db_path) as database:
            row = database.execute(
                "SELECT input_tokens + output_tokens FROM daily_usage WHERE day = ?",
                (self._today(),),
            ).fetchone()
        return int(row[0]) if row else 0

    def _record_usage(self, usage: dict[str, int]) -> None:
        input_tokens = max(0, int(usage.get("input_tokens", 0)))
        output_tokens = max(0, int(usage.get("output_tokens", 0)))
        with sqlite3.connect(self.usage_db_path) as database:
            database.execute(
                """INSERT INTO daily_usage(day, input_tokens, output_tokens)
                   VALUES (?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens""",
                (self._today(), input_tokens, output_tokens),
            )

    def _budget_exhausted(self) -> bool:
        return bool(
            self.daily_token_limit
            and self._tokens_used_today() >= self.daily_token_limit
        )

    @staticmethod
    def _to_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in history:
            role = item["role"]
            if role == "user":
                items.append({"role": "user", "content": item["content"]})
            elif role == "assistant":
                if item.get("provider") == NAME and item.get("raw") is not None:
                    items.extend(
                        output.model_dump(exclude_none=True)
                        if hasattr(output, "model_dump")
                        else output
                        for output in item["raw"]
                    )
                    continue
                if item.get("text"):
                    items.append({"role": "assistant", "content": item["text"]})
                for call in item.get("tool_calls", []):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call["id"],
                            "name": call["name"],
                            "arguments": json.dumps(
                                call["input"], ensure_ascii=False
                            ),
                        }
                    )
            elif role == "tool":
                for result in item["results"]:
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": result["id"],
                            "output": result["content"],
                        }
                    )
        return items

    @staticmethod
    def _to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
                "strict": False,
            }
            for tool in tools
        ]

    def _kwargs(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": self._to_input(self._bounded_history(history)),
            "max_output_tokens": self.max_tokens,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"verbosity": self.verbosity},
            "store": False,
        }
        if tools:
            kwargs["tools"] = self._to_tools(tools)
        return kwargs

    @staticmethod
    def _normalize(response: Any, streamed_text: str = "") -> LLMResponse:
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") == "function_call":
                try:
                    arguments = json.loads(item.arguments or "{}")
                except (json.JSONDecodeError, ValueError):
                    arguments = {}
                tool_calls.append(
                    ToolCall(
                        id=item.call_id,
                        name=item.name,
                        input=arguments,
                    )
                )
            elif getattr(item, "type", "") == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", "") == "output_text":
                        text_parts.append(content.text)

        text = streamed_text or "".join(text_parts).strip()
        if tool_calls:
            text = ""
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            provider=NAME,
            assistant_content=getattr(response, "output", None),
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
            },
        )

    async def complete(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: TextDeltaFn | None = None,
    ) -> LLMResponse:
        if self._budget_exhausted():
            return LLMResponse(
                text=(
                    "He alcanzado el límite diario configurado para OpenAI. "
                    "La escucha y la voz local continúan disponibles."
                ),
                provider=NAME,
                stop_reason="budget_exhausted",
            )
        kwargs = self._kwargs(system, history, tools)
        if on_text_delta is None:
            response = await self._client.responses.create(**kwargs)
            normalized = self._normalize(response)
            self._record_usage(normalized.usage)
            return normalized

        text_parts: list[str] = []
        final_response: Any = None
        async with self._client.responses.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    text_parts.append(event.delta)
                    await on_text_delta(event.delta)
            final_response = await stream.get_final_response()
        normalized = self._normalize(final_response, "".join(text_parts).strip())
        self._record_usage(normalized.usage)
        return normalized

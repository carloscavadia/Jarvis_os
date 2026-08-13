"""Pruebas del proveedor OpenAI nativo sin consumir la API."""

import json
from types import SimpleNamespace

from jarvis_core.config import Settings
from jarvis_core.llm.factory import build_llm
from jarvis_core.llm.openai_responses import NAME, OpenAIResponsesProvider


def provider(tmp_path=None) -> OpenAIResponsesProvider:
    instance = object.__new__(OpenAIResponsesProvider)
    instance.model = "gpt-5.4-nano"
    instance.max_tokens = 900
    instance.reasoning_effort = "low"
    instance.verbosity = "low"
    instance.daily_token_limit = 100_000
    instance.history_items = 24
    instance.usage_db_path = str(
        (tmp_path / "usage.db") if tmp_path else ":memory:"
    )
    if tmp_path:
        instance._prepare_usage_db()
    return instance


def test_local_first_request_is_stateless_and_bounded():
    kwargs = provider()._kwargs(
        "Responde en español.",
        [{"role": "user", "content": "Estado del servidor"}],
        [],
    )

    assert kwargs["model"] == "gpt-5.4-nano"
    assert kwargs["store"] is False
    assert kwargs["max_output_tokens"] == 900
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["text"] == {"verbosity": "low"}


def test_responses_tools_and_results_preserve_call_id():
    converted = provider()._to_input(
        [
            {
                "role": "assistant",
                "text": "",
                "tool_calls": [
                    {"id": "call_1", "name": "system_info", "input": {"section": "disk"}}
                ],
            },
            {
                "role": "tool",
                "results": [
                    {"id": "call_1", "name": "system_info", "content": "42%", "is_error": False}
                ],
            },
        ]
    )

    assert converted[0]["type"] == "function_call"
    assert json.loads(converted[0]["arguments"]) == {"section": "disk"}
    assert converted[1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "42%",
    }


def test_normalize_function_call_and_usage():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_2",
                name="list_files",
                arguments='{"path":"."}',
            )
        ],
        usage=SimpleNamespace(input_tokens=120, output_tokens=15),
    )

    result = provider()._normalize(response)

    assert result.provider == NAME
    assert result.stop_reason == "tool_use"
    assert result.text == ""
    assert result.tool_calls[0].input == {"path": "."}
    assert result.usage == {"input_tokens": 120, "output_tokens": 15}


def test_factory_selects_native_responses_provider(monkeypatch):
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: SimpleNamespace())
    settings = Settings(
        llm_provider="openai_responses",
        openai_responses_api_key="test-key",
        openai_usage_db_path=":memory:",
    )

    result = build_llm(settings)

    assert isinstance(result, OpenAIResponsesProvider)


def test_daily_usage_budget_is_persistent(tmp_path):
    instance = provider(tmp_path)
    instance.daily_token_limit = 150

    instance._record_usage({"input_tokens": 100, "output_tokens": 49})
    assert instance._tokens_used_today() == 149
    assert instance._budget_exhausted() is False

    instance._record_usage({"input_tokens": 1, "output_tokens": 0})
    assert instance._budget_exhausted() is True


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return self.response


async def test_budget_stops_api_call_but_keeps_local_runtime_available(tmp_path):
    instance = provider(tmp_path)
    instance.daily_token_limit = 1
    instance._record_usage({"input_tokens": 1, "output_tokens": 0})
    fake = FakeResponses(SimpleNamespace(output=[], usage=None))
    instance._client = SimpleNamespace(responses=fake)

    result = await instance.complete("sistema", [], [])

    assert fake.calls == 0
    assert result.stop_reason == "budget_exhausted"
    assert "escucha y la voz local" in result.text


def test_history_sent_to_openai_is_bounded_from_a_user_turn():
    instance = provider()
    instance.history_items = 4
    history = [
        {"role": "user", "content": "uno"},
        {"role": "assistant", "text": "uno", "tool_calls": []},
        {"role": "user", "content": "dos"},
        {"role": "assistant", "text": "dos", "tool_calls": []},
        {"role": "user", "content": "tres"},
        {"role": "assistant", "text": "tres", "tool_calls": []},
    ]

    bounded = instance._bounded_history(history)

    assert len(bounded) == 4
    assert bounded[0] == {"role": "user", "content": "dos"}

"""Pruebas del contrato incremental sin consumir un proveedor real."""

from types import SimpleNamespace

from jarvis_core.llm.openai_compatible import OpenAICompatibleProvider


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iterator = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        assert kwargs["stream"] is True
        return FakeStream(self._chunks)


def chunk(*, content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def tool_delta(index, *, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


async def test_openai_stream_emits_text_and_reassembles_tool_calls():
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="test/model",
        base_url="https://example.invalid/v1",
    )
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=FakeCompletions(
                [
                    chunk(content="Hola "),
                    chunk(content="jefe"),
                    chunk(
                        tool_calls=[
                            tool_delta(
                                0,
                                call_id="call_1",
                                name="system_info",
                                arguments='{"section":',
                            )
                        ]
                    ),
                    chunk(
                        tool_calls=[tool_delta(0, arguments='"time"}')],
                        finish_reason="tool_calls",
                    ),
                ]
            )
        )
    )
    deltas = []

    async def receive(delta):
        deltas.append(delta)

    response = await provider.complete("sistema", [], [], on_text_delta=receive)

    assert deltas == ["Hola ", "jefe"]
    assert response.text == "Hola jefe"
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "system_info"
    assert response.tool_calls[0].input == {"section": "time"}

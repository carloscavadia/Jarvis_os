"""Tests de la conversación Realtime alojada en el gateway.

No hablan con OpenAI: inyectan un doble de la conexión para comprobar la
configuración de sesión, el reparto de eventos, la ejecución de herramientas y
—lo que más importa— que una acción sensible no se ejecuta sin aprobación.
"""

import asyncio
import base64
from typing import ClassVar

import pytest
from jarvis_core.config import Settings
from jarvis_core.tools.base import Tool, ToolRegistry, ToolResult
from jarvis_gateway.realtime_session import RealtimeConversation


class Event:
    """Imita los eventos pydantic del SDK: atributos, no claves."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeConnection:
    def __init__(self, events=()):
        self.events = list(events)
        self.session = self._Session(self)
        self.response = self._Response(self)
        self.input_audio_buffer = self._Buffer(self)
        self.conversation = self._Conversation(self)
        self.session_config = None
        self.appended: list[str] = []
        self.items: list[dict] = []
        self.responses_created = 0
        self.cancels = 0

    class _Session:
        def __init__(self, outer):
            self.outer = outer

        async def update(self, session):
            self.outer.session_config = session

    class _Response:
        def __init__(self, outer):
            self.outer = outer

        async def create(self):
            self.outer.responses_created += 1

        async def cancel(self):
            self.outer.cancels += 1

    class _Buffer:
        def __init__(self, outer):
            self.outer = outer

        async def append(self, audio):
            self.outer.appended.append(audio)

    class _Conversation:
        def __init__(self, outer):
            self.item = FakeConnection._Item(outer)

    class _Item:
        def __init__(self, outer):
            self.outer = outer

        async def create(self, item):
            self.outer.items.append(item)

    def __aiter__(self):
        async def gen():
            for event in self.events:
                yield event

        return gen()


class EchoTool(Tool):
    name = "eco"
    description = "Devuelve lo que le des."
    input_schema: ClassVar[dict] = {"type": "object", "properties": {"texto": {"type": "string"}}}

    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResult(f"eco:{kwargs.get('texto', '')}")


class DangerousTool(EchoTool):
    name = "borrar_todo"
    description = "Acción sensible."
    requires_confirmation = True


def build(events=(), tools=(), confirm=None):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    audio: list[bytes] = []
    sent: list[dict] = []

    async def on_audio(pcm):
        audio.append(pcm)

    async def on_event(payload):
        sent.append(payload)

    settings = Settings(openai_responses_api_key="sk-test")
    conversation = RealtimeConversation(
        settings, registry, on_audio=on_audio, on_event=on_event, confirm=confirm
    )
    connection = FakeConnection(events)
    conversation._connection = connection
    return conversation, connection, audio, sent


# ── Configuración de la sesión ───────────────────────────────────────────────


def test_session_config_uses_pcm_24k_in_both_directions():
    conversation, _, _, _ = build()
    config = conversation.session_config()
    assert config["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert config["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert config["output_modalities"] == ["audio"]


def test_session_config_publishes_every_registered_tool():
    conversation, _, _, _ = build(tools=[EchoTool(), DangerousTool()])
    tools = conversation.session_config()["tools"]
    assert {tool["name"] for tool in tools} == {"eco", "borrar_todo"}
    assert all(tool["type"] == "function" for tool in tools)
    # El esquema viaja como `parameters`, no como `input_schema`.
    assert tools[0]["parameters"]["type"] == "object"


def test_session_config_keeps_the_shared_persona():
    conversation, _, _, _ = build()
    instructions = conversation.session_config()["instructions"]
    assert "JARVIS" in instructions
    assert "voz" in instructions  # guía específica del canal hablado


def test_server_vad_closes_turns_so_the_device_does_not_have_to():
    conversation, _, _, _ = build()
    detection = conversation.session_config()["audio"]["input"]["turn_detection"]
    assert detection["type"] == "server_vad"
    assert detection["create_response"] is True
    assert detection["interrupt_response"] is True


# ── Reparto de eventos ───────────────────────────────────────────────────────


async def test_audio_deltas_reach_the_device_decoded():
    pcm = b"\x01\x02\x03\x04"
    event = Event(type="response.output_audio.delta", delta=base64.b64encode(pcm).decode())
    conversation, _, audio, _ = build([event])
    await conversation.pump()
    assert audio == [pcm]


async def test_speech_events_drive_the_hud_state():
    conversation, _, _, sent = build(
        [
            Event(type="input_audio_buffer.speech_started"),
            Event(type="input_audio_buffer.speech_stopped"),
        ]
    )
    await conversation.pump()
    assert [item["state"] for item in sent] == ["listening", "thinking"]


async def test_transcripts_are_forwarded_for_the_log():
    conversation, _, _, sent = build(
        [
            Event(
                type="conversation.item.input_audio_transcription.completed",
                transcript="qué hora es",
            ),
            Event(type="response.output_audio_transcript.delta", delta="Son las"),
        ]
    )
    await conversation.pump()
    assert sent[0] == {"type": "transcript", "text": "qué hora es"}
    assert sent[1] == {"type": "reply_delta", "delta": "Son las"}


async def test_errors_are_reported_instead_of_killing_the_session():
    conversation, _, _, sent = build(
        [Event(type="error", error=Event(message="límite alcanzado"))]
    )
    await conversation.pump()
    assert sent == [{"type": "error", "error": "límite alcanzado"}]


async def test_usage_is_accumulated_for_the_budget():
    usage = Event(
        input_tokens=100,
        output_tokens=50,
        input_token_details=Event(audio_tokens=80, cached_tokens=60),
        output_token_details=Event(audio_tokens=40),
    )
    conversation, _, _, _ = build(
        [Event(type="response.done", response=Event(usage=usage))]
    )
    await conversation.pump()
    assert conversation.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "input_audio_tokens": 80,
        "output_audio_tokens": 40,
        "cached_tokens": 60,
    }


# ── Audio de entrada ─────────────────────────────────────────────────────────


async def test_audio_is_sent_base64_encoded():
    conversation, connection, _, _ = build()
    await conversation.send_audio(b"\xaa\xbb")
    assert connection.appended == [base64.b64encode(b"\xaa\xbb").decode()]


# ── Herramientas ─────────────────────────────────────────────────────────────


async def _drain(conversation):
    """Espera a las tareas de herramienta, que corren fuera del bucle."""
    for _ in range(50):
        if not conversation._tool_tasks:
            return
        await asyncio.sleep(0)
    raise AssertionError("las herramientas no terminaron")


async def test_tool_runs_and_its_output_goes_back_to_openai():
    tool = EchoTool()
    conversation, connection, _, sent = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-1",
                name="eco",
                arguments='{"texto": "hola"}',
            )
        ],
        tools=[tool],
    )
    await conversation.pump()
    await _drain(conversation)

    assert tool.calls == [{"texto": "hola"}]
    assert connection.items == [
        {"type": "function_call_output", "call_id": "call-1", "output": "eco:hola"}
    ]
    # Sin `response.create` JARVIS se quedaría callado tras usar la herramienta.
    assert connection.responses_created == 1
    assert [item.get("phase") for item in sent] == ["proposed", "running", "completed"]


async def test_sensitive_tool_never_runs_without_approval():
    tool = DangerousTool()

    async def deny(name, arguments):
        return False

    conversation, connection, _, sent = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-2",
                name="borrar_todo",
                arguments="{}",
            )
        ],
        tools=[tool],
        confirm=deny,
    )
    await conversation.pump()
    await _drain(conversation)

    assert tool.calls == []
    assert connection.items[0]["output"] == "El usuario denegó la acción."
    assert any(item.get("phase") == "denied" for item in sent)


async def test_sensitive_tool_runs_once_approved():
    tool = DangerousTool()
    asked = []

    async def allow(name, arguments):
        asked.append(name)
        return True

    conversation, _, _, _ = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-3",
                name="borrar_todo",
                arguments="{}",
            )
        ],
        tools=[tool],
        confirm=allow,
    )
    await conversation.pump()
    await _drain(conversation)

    assert asked == ["borrar_todo"]
    assert tool.calls == [{}]


async def test_missing_confirmer_denies_instead_of_running():
    # Si el canal no puede pedir aprobación, lo seguro es no ejecutar.
    tool = DangerousTool()
    conversation, connection, _, _ = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-4",
                name="borrar_todo",
                arguments="{}",
            )
        ],
        tools=[tool],
        confirm=None,
    )
    await conversation.pump()
    await _drain(conversation)

    assert tool.calls == []
    assert connection.items[0]["output"] == "El usuario denegó la acción."


async def test_unknown_tool_and_bad_arguments_are_answered_not_crashed():
    conversation, connection, _, _ = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-5",
                name="inexistente",
                arguments="{}",
            ),
            Event(
                type="response.function_call_arguments.done",
                call_id="call-6",
                name="eco",
                arguments="no-es-json",
            ),
        ],
        tools=[EchoTool()],
    )
    await conversation.pump()
    await _drain(conversation)

    outputs = {item["call_id"]: item["output"] for item in connection.items}
    assert "desconocida" in outputs["call-5"]
    assert "inválidos" in outputs["call-6"]


async def test_tool_output_is_truncated_before_reaching_the_model():
    class Verbose(EchoTool):
        name = "verboso"

        async def run(self, **kwargs):
            return ToolResult("x" * 50000)

    conversation, connection, _, _ = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-7",
                name="verboso",
                arguments="{}",
            )
        ],
        tools=[Verbose()],
    )
    await conversation.pump()
    await _drain(conversation)

    assert len(connection.items[0]["output"]) == Settings().realtime_max_tool_output


async def test_a_slow_tool_does_not_block_incoming_audio():
    """El audio ya en curso debe seguir sonando mientras la herramienta trabaja."""
    release = asyncio.Event()

    class Slow(EchoTool):
        name = "lento"

        async def run(self, **kwargs):
            await release.wait()
            return ToolResult("listo")

    pcm = base64.b64encode(b"\x09\x09").decode()
    conversation, connection, audio, _ = build(
        [
            Event(
                type="response.function_call_arguments.done",
                call_id="call-8",
                name="lento",
                arguments="{}",
            ),
            Event(type="response.output_audio.delta", delta=pcm),
        ],
        tools=[Slow()],
    )
    await conversation.pump()

    # El audio llegó al dispositivo aunque la herramienta sigue sin terminar.
    assert audio == [b"\x09\x09"]
    assert conversation._tool_tasks and connection.items == []

    release.set()
    await _drain(conversation)
    assert connection.items[0]["output"] == "listo"


async def test_connect_requires_an_api_key():
    registry = ToolRegistry()

    async def noop(*args):
        pass

    conversation = RealtimeConversation(
        Settings(openai_responses_api_key=""),
        registry,
        on_audio=noop,
        on_event=noop,
    )
    with pytest.raises(Exception, match="OPENAI_API_KEY"):
        await conversation.connect()


async def test_cancel_interrupts_the_current_response():
    conversation, connection, _, _ = build()
    await conversation.cancel_response()
    assert connection.cancels == 1

"""Caché de prompt, streaming real y canal de sistema del proveedor de Claude.

Tres cosas que faltaban y que se sostienen entre sí:

- **No había caché.** El prompt de sistema y las herramientas se repiten palabra
  por palabra en cada vuelta del bucle —hasta 12 por turno— y se pagaban enteras
  cada vez.
- **El streaming era simulado.** Se pedía la respuesta completa y se entregaba de
  golpe al canal, así que el HUD la pintaba de una vez en lugar de en karaoke, y
  la voz no podía empezar antes.
- **La capa volátil.** Va como mensaje de sistema a mitad de conversación para no
  tocar el prefijo cacheado. Sonnet 5 no lo admite, y ahí hay que replegarse.
"""

import pytest
from jarvis_core.llm.anthropic_provider import AnthropicProvider


class _Bloque:
    def __init__(self, texto):
        self.type = "text"
        self.text = texto


class _Mensaje:
    stop_reason = "end_turn"

    def __init__(self, texto):
        self.content = [_Bloque(texto)]
        self.usage = type(
            "U", (), {"input_tokens": 10, "output_tokens": 5,
                      "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}
        )()


class _Stream:
    def __init__(self, trozos, registro):
        self._trozos, self._registro = trozos, registro

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for trozo in self._trozos:
                yield trozo
        return gen()

    async def get_final_message(self):
        return _Mensaje("".join(self._trozos))


class _ClienteFalso:
    def __init__(self, trozos=("Hola, ", "jefe.")):
        self.peticiones: list[dict] = []
        self.messages = self
        self._trozos = trozos

    def stream(self, **kwargs):
        self.peticiones.append(kwargs)
        return _Stream(self._trozos, self.peticiones)


def _proveedor(modelo="claude-opus-5", trozos=("Hola, ", "jefe.")):
    p = AnthropicProvider.__new__(AnthropicProvider)
    p.model, p.max_tokens, p.effort = modelo, 16000, "high"
    p._client = _ClienteFalso(trozos)
    return p


HISTORIA = [{"role": "user", "content": "hola"}]


async def test_el_prompt_de_sistema_se_manda_con_punto_de_cache():
    proveedor = _proveedor()

    await proveedor.complete(system="Eres JARVIS.", history=HISTORIA, tools=[])

    system = proveedor._client.peticiones[0]["system"]
    assert isinstance(system, list), "sin bloques no se puede marcar dónde cachear"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "Eres JARVIS."


async def test_se_reportan_los_tokens_leidos_de_cache():
    """Si esto sale cero llamada tras llamada, algo invalida el prefijo."""
    proveedor = _proveedor()

    respuesta = await proveedor.complete(system="s", history=HISTORIA, tools=[])

    assert respuesta.usage["cache_read_input_tokens"] == 900


async def test_el_texto_llega_en_fragmentos_no_de_golpe():
    proveedor = _proveedor(trozos=("He ", "encendido ", "la luz."))
    recibidos = []

    async def delta(trozo):
        recibidos.append(trozo)

    respuesta = await proveedor.complete(
        system="s", history=HISTORIA, tools=[], on_text_delta=delta
    )

    assert recibidos == ["He ", "encendido ", "la luz."], "el streaming sigue siendo simulado"
    assert respuesta.text == "He encendido la luz."


async def test_sin_receptor_de_texto_no_se_rompe():
    proveedor = _proveedor()
    respuesta = await proveedor.complete(system="s", history=HISTORIA, tools=[])
    assert respuesta.text == "Hola, jefe."


async def test_la_capa_volatil_va_como_mensaje_de_sistema_en_opus5():
    proveedor = _proveedor("claude-opus-5")

    await proveedor.complete(
        system="Eres JARVIS.", history=HISTORIA, tools=[], system_overlay="Son las 10:00."
    )

    peticion = proveedor._client.peticiones[0]
    # El prefijo queda intacto: eso es lo que hace que la caché sirva de algo.
    assert peticion["system"][0]["text"] == "Eres JARVIS."
    assert peticion["messages"][-1] == {"role": "system", "content": "Son las 10:00."}


@pytest.mark.parametrize("modelo", ["claude-sonnet-5", "claude-haiku-4-5"])
async def test_en_modelos_sin_ese_canal_la_capa_se_repliega(modelo):
    """Mandárselo a Sonnet 5 es un error de petición, no una degradación."""
    proveedor = _proveedor(modelo)

    await proveedor.complete(
        system="Eres JARVIS.", history=HISTORIA, tools=[], system_overlay="Son las 10:00."
    )

    peticion = proveedor._client.peticiones[0]
    assert "Son las 10:00." in peticion["system"][0]["text"]
    assert all(m["role"] != "system" for m in peticion["messages"])


async def test_tras_un_resultado_de_herramienta_la_capa_sigue_siendo_valida():
    """Un `tool_result` viaja como mensaje de usuario, así que cumple la regla
    de que el mensaje de sistema debe seguir a uno de usuario."""
    proveedor = _proveedor("claude-opus-5")
    historia = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "text": "", "tool_calls": [{"id": "t", "name": "x", "input": {}}]},
        {"role": "tool", "results": [{"id": "t", "name": "x", "content": "ok", "is_error": False}]},
    ]

    await proveedor.complete(
        system="Eres JARVIS.", history=historia, tools=[], system_overlay="Son las 10:00."
    )

    peticion = proveedor._client.peticiones[0]
    assert peticion["messages"][-1] == {"role": "system", "content": "Son las 10:00."}
    assert peticion["system"][0]["text"] == "Eres JARVIS."


async def test_si_el_ultimo_turno_es_del_asistente_la_capa_se_repliega():
    """La regla exige que siga a un mensaje de usuario. Guarda defensiva."""
    proveedor = _proveedor("claude-opus-5")
    historia = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "text": "Dime.", "tool_calls": []},
    ]

    await proveedor.complete(
        system="Eres JARVIS.", history=historia, tools=[], system_overlay="Son las 10:00."
    )

    peticion = proveedor._client.peticiones[0]
    assert "Son las 10:00." in peticion["system"][0]["text"]
    assert all(m["role"] != "system" for m in peticion["messages"])

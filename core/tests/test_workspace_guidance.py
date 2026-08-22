"""Lo que se le dice a JARVIS sobre el pizarrón.

Ante «lista mis dispositivos de la casa y de Home Assistant», JARVIS respondió
«aquí tienes un resumen… dejé el detalle completo en el pizarrón» y no dijo ni
cuántos dispositivos había ni de qué tipo. No fue un fallo del modelo: el prompt
le pedía exactamente eso —«indica que el detalle está visible en el pizarrón»—,
así que anunciar en vez de responder era la conducta premiada.

Estos tests fijan las dos instrucciones que lo corrigen, para que un retoque
posterior del prompt no las deshaga sin darse cuenta.
"""

from jarvis_core.config import Settings
from jarvis_core.tools.builtin.presentation import ShowInWorkspaceTool


def _prompt() -> str:
    return Settings(language="es", hud_workspace_enabled=True).system_prompt()


def test_sin_el_pizarron_activo_no_se_habla_de_el():
    assert "show_in_workspace" not in Settings(hud_workspace_enabled=False).system_prompt()


def test_el_analisis_va_en_el_chat_no_solo_el_aviso():
    prompt = _prompt()
    assert "el análisis va en el chat" in prompt
    # La frase que causaba el problema no debe volver.
    assert "indica que el detalle está visible en el pizarrón" not in prompt


def test_se_dice_que_es_lo_que_hay_que_resumir_de_una_lista():
    prompt = _prompt()
    for exigencia in ("cuántos elementos hay", "por categoría", "qué destaca"):
        assert exigencia in prompt, f"falta pedir: {exigencia}"


def test_anunciar_sin_responder_esta_explicitamente_prohibido():
    assert "no es una respuesta" in _prompt()


def test_las_listas_deben_ir_como_json_estructurado():
    prompt = _prompt()
    assert "array de objetos" in prompt
    assert "las mismas claves" in prompt
    # El motivo importa tanto como la regla: si no se explica, se ignora.
    assert "agrupa, cuenta y filtra" in prompt


def test_la_herramienta_lo_repite_donde_el_modelo_lo_lee_en_cada_llamada():
    definicion = ShowInWorkspaceTool().definition()
    assert "array de objetos" in definicion["description"]
    assert "json" in definicion["input_schema"]["properties"]["format"]["description"]
    assert "nombre legible" in definicion["input_schema"]["properties"]["content"]["description"]

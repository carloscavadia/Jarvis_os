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
    assert "el chat lleva la lectura" in prompt
    # La frase que causaba el problema no debe volver.
    assert "indica que el detalle está visible en el pizarrón" not in prompt


def test_chat_y_pizarron_se_reparten_el_trabajo_sin_solaparse():
    """No son dos copias del mismo contenido: uno lleva los datos, otro su lectura."""
    prompt = _prompt()
    assert "complementarios, nunca redundantes" in prompt
    assert "el pizarrón lleva los datos" in prompt
    # La regla operativa, no solo el principio.
    assert "no lo escribas en el chat" in prompt
    assert "no enumeres allí elementos" in prompt
    # Y en el sentido contrario.
    assert "el pizarrón no repite tu conclusión" in prompt


def test_se_dice_que_es_lo_que_hay_que_resumir_de_una_lista():
    prompt = _prompt()
    for exigencia in ("cuántos elementos hay", "por categoría", "qué destaca"):
        assert exigencia in prompt, f"falta pedir: {exigencia}"


def test_anunciar_sin_responder_esta_explicitamente_prohibido():
    assert "no es una respuesta" in _prompt()


def test_no_se_copia_al_pizarron_lo_que_ya_muestra_una_herramienta():
    """Pedirlo costaba miles de tokens de regeneración y colgaba el turno.

    Con 148 entidades de Home Assistant, el modelo tenía que reescribir la lista
    entera como JSON para pasarla a `show_in_workspace` —hasta 12.000 caracteres
    por esquema—. El HUD se quedaba minutos en «JARVIS está respondiendo…»
    generando algo que el pizarrón ya mostraba solo desde el evento de la
    herramienta.
    """
    prompt = _prompt()
    assert "ya aparece solo en el pizarrón" in prompt
    assert "No lo copies" in prompt
    assert "Úsalo únicamente para contenido que compongas tú" in prompt
    assert "No lo uses para repetir lo que devolvió otra herramienta" in (
        ShowInWorkspaceTool().definition()["description"]
    )


def test_las_listas_propias_deben_ir_como_json_estructurado():
    prompt = _prompt()
    assert "array de objetos" in prompt
    assert "las mismas claves" in prompt
    # El motivo importa tanto como la regla: si no se explica, se ignora.
    assert "agrupa, cuenta y filtra" in prompt


def test_la_herramienta_lo_repite_donde_el_modelo_lo_lee_en_cada_llamada():
    definicion = ShowInWorkspaceTool().definition()
    assert "array de objetos" in definicion["description"]
    assert "se reparten el trabajo sin solaparse" in definicion["description"]
    assert "json" in definicion["input_schema"]["properties"]["format"]["description"]
    assert "nombre legible" in definicion["input_schema"]["properties"]["content"]["description"]

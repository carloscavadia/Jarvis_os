"""Cómo llega al HUD la salida de una herramienta grande.

El recorte era `content[:12000]`, un corte por caracteres. Con el inventario de
Home Assistant —148 entidades— el JSON llegaba partido a mitad de un objeto: el
pizarrón no podía parsearlo, caía al modo texto y pintaba una pared de llaves.
Lo que se fija aquí es que lo que llega **sigue siendo JSON válido**, aunque
lleve menos registros, para que el HUD pueda analizarlo y presentarlo.
"""

import json

from jarvis_gateway.app import _TOOL_OUTPUT_MAX_CHARS, _public_tool_output


def _inventario(cantidad: int) -> str:
    return json.dumps(
        {
            "total": cantidad,
            "returned": cantidad,
            "offset": 0,
            "has_more": False,
            "entities": [
                {
                    "entity_id": f"sensor.backup_last_successful_automatic_backup_{i}",
                    "name": f"Backup Last successful automatic backup {i}",
                    "domain": "sensor",
                    "state": "unknown",
                    "device_class": "timestamp",
                }
                for i in range(cantidad)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_lo_que_cabe_pasa_intacto():
    contenido = _inventario(5)
    assert len(contenido) <= _TOOL_OUTPUT_MAX_CHARS
    salida, recorte = _public_tool_output(contenido)
    assert salida == contenido
    assert recorte is None


def test_un_inventario_grande_llega_recortado_pero_parseable():
    contenido = _inventario(148)
    assert len(contenido) > _TOOL_OUTPUT_MAX_CHARS, "el caso de prueba debe desbordar"

    salida, recorte = _public_tool_output(contenido)

    assert len(salida) <= _TOOL_OUTPUT_MAX_CHARS
    datos = json.loads(salida)  # <- esto es exactamente lo que fallaba antes
    assert 0 < len(datos["entities"]) < 148
    # El total declarado no se toca: el HUD debe poder decir «60 de 148».
    assert datos["total"] == 148
    assert recorte == {"kind": "json", "shown": len(datos["entities"]), "total": 148}


def test_el_texto_plano_se_corta_por_lineas_enteras():
    contenido = "una linea de registro\n" * 2000
    salida, recorte = _public_tool_output(contenido)

    assert len(salida) <= _TOOL_OUTPUT_MAX_CHARS
    assert not salida.endswith("una lin"), "cortó una línea a medias"
    assert salida.endswith("una linea de registro")
    assert recorte["kind"] == "text"
    assert recorte["total"] == len(contenido)


def test_un_json_invalido_no_rompe_el_recorte():
    contenido = "{roto: " + "x" * 20000
    salida, recorte = _public_tool_output(contenido)
    assert len(salida) <= _TOOL_OUTPUT_MAX_CHARS
    assert recorte["kind"] == "text"


def test_una_lista_suelta_tambien_se_reduce_sin_romperse():
    contenido = json.dumps(
        [{"id": i, "nombre": f"elemento numero {i}"} for i in range(3000)],
        separators=(",", ":"),
    )
    salida, recorte = _public_tool_output(contenido)

    datos = json.loads(salida)
    assert isinstance(datos, list) and 0 < len(datos) < 3000
    assert recorte["kind"] == "json"


def test_una_presentacion_con_una_lista_larga_llega_como_json_valido():
    """El camino deliberado: JARVIS llama a `show_in_workspace` con la lista.

    Antes este camino tenía el mismo corte por caracteres, así que una lista que
    se pasara de largo llegaba rota igual que la salida automática.
    """
    from jarvis_gateway.app import _workspace_presentation

    entidades = [
        {
            "entity_id": f"light.dispositivo_del_salon_numero_{i}",
            "name": f"Lámpara del salón número {i}",
            "domain": "light",
            "state": "on" if i % 2 else "off",
        }
        for i in range(300)
    ]
    contenido = json.dumps(entidades, ensure_ascii=False, separators=(",", ":"))
    assert len(contenido) > _TOOL_OUTPUT_MAX_CHARS

    presentacion = _workspace_presentation(
        "show_in_workspace",
        {"title": "Dispositivos", "content": contenido, "format": "json"},
    )

    assert presentacion["format"] == "json"
    datos = json.loads(presentacion["content"])  # el pizarrón hace justo esto
    assert 0 < len(datos) < 300
    assert presentacion["clipped"]["total"] == 300

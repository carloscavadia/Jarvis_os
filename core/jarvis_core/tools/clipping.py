"""Recorte de salidas de herramienta que no rompe su estructura.

Un corte por caracteres parte un JSON a mitad de un objeto: quien lo recibe ya
no puede parsearlo. Eso le pasaba al pizarrón del HUD con el inventario de Home
Assistant, y le pasa igual al modelo cuando el resultado se le devuelve
recortado. Aquí el recorte reduce el número de elementos de las listas hasta que
entra, de forma que lo que sale sigue siendo JSON válido.

Lo usan los dos consumidores de una salida de herramienta: el gateway, que la
manda al HUD, y el orquestador, que la devuelve al modelo.
"""

from __future__ import annotations

import json

#: Cuántos elementos se conservan de cada lista al reducir, de más a menos.
_ARRAY_STEPS = (400, 250, 150, 100, 60, 40, 25, 15, 8, 4, 2, 1)


def count_items(value: object) -> int:
    """Elementos de lista que hay en toda la estructura, a cualquier profundidad."""
    if isinstance(value, list):
        return len(value) + sum(count_items(item) for item in value)
    if isinstance(value, dict):
        return sum(count_items(item) for item in value.values())
    return 0


def _shrink_arrays(value: object, keep: int) -> object:
    if isinstance(value, list):
        return [_shrink_arrays(item, keep) for item in value[:keep]]
    if isinstance(value, dict):
        return {key: _shrink_arrays(item, keep) for key, item in value.items()}
    return value


def clip_structured(content: str, max_chars: int) -> tuple[str, dict[str, object] | None]:
    """Recorta `content` a `max_chars` conservando su estructura si es JSON.

    Devuelve el texto recortado y, cuando se dejó algo fuera, un resumen de qué:
    `{"kind": "json"|"text", "shown": …, "total": …}`.
    """
    if len(content) <= max_chars:
        return content, None

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, (dict, list)):
        total_items = count_items(parsed)
        for keep in _ARRAY_STEPS:
            reduced = _shrink_arrays(parsed, keep)
            text = json.dumps(reduced, ensure_ascii=False, separators=(",", ":"))
            if len(text) <= max_chars:
                kept_items = count_items(reduced)
                if kept_items >= total_items:
                    return text, None
                return text, {
                    "kind": "json",
                    "shown": kept_items,
                    "total": total_items,
                }

    # No es JSON (o ni con un elemento por lista cabe): se corta por líneas para
    # no partir una a medias, y se avisa.
    clipped = content[:max_chars]
    newline = clipped.rfind("\n")
    if newline > max_chars // 2:
        clipped = clipped[:newline]
    return clipped, {"kind": "text", "shown": len(clipped), "total": len(content)}

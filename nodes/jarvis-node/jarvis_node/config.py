"""Configuración de la máquina, leída de un fichero JSON.

Vive en un fichero y no en variables de entorno porque declara rutas y listas de
programas: cosas que se revisan, se comentan y se versionan por máquina. Un `.env` de
una línea invita a pegar `allow_writes=true` sin mirar qué abre.

Todo lo peligroso está apagado por defecto. Un fichero vacío da un nodo que solo sabe
decir quién es.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis_node.capabilities import NodeCapabilities

DEFAULT_CONFIG_PATH = Path.home() / ".jarvis-node.json"


class ConfigError(ValueError):
    """La configuración no se puede aplicar."""


def load_capabilities(path: str | Path | None = None) -> NodeCapabilities:
    ruta = Path(path) if path else DEFAULT_CONFIG_PATH
    if not ruta.is_file():
        raise ConfigError(
            f"No encuentro la configuración en '{ruta}'. "
            "Crea el fichero antes de arrancar el nodo."
        )
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"'{ruta}' no es JSON válido: {exc}") from exc
    if not isinstance(datos, dict):
        raise ConfigError(f"'{ruta}' debe contener un objeto.")

    node_id = str(datos.get("node_id") or "").strip()
    if not node_id:
        raise ConfigError("Falta 'node_id': es la dirección del nodo en los topics.")
    # El id viaja dentro de un topic MQTT, así que un '/' o un comodín lo partirían o
    # lo harían coincidir con topics ajenos.
    if any(c in node_id for c in "/+#") or len(node_id) > 64:
        raise ConfigError(
            "'node_id' no puede contener '/', '+' ni '#', y debe tener 64 caracteres o menos."
        )

    def rutas(clave: str) -> list[Path]:
        crudas = datos.get(clave) or []
        if not isinstance(crudas, list):
            raise ConfigError(f"'{clave}' debe ser una lista.")
        return [Path(str(r)).expanduser() for r in crudas]

    write_roots = rutas("write_roots")
    allow_writes = bool(datos.get("allow_writes", False))
    if write_roots and not allow_writes:
        # Declarar dónde escribir y no activar la escritura es casi siempre un
        # despiste. Decirlo es mejor que arrancar en un estado que no es el que el
        # dueño creía haber pedido.
        raise ConfigError(
            "Hay 'write_roots' pero 'allow_writes' es false: activa la escritura "
            "explícitamente o quita las raíces."
        )

    return NodeCapabilities(
        node_id=node_id,
        read_roots=rutas("read_roots"),
        write_roots=write_roots,
        allowed_executables=[str(e) for e in (datos.get("executables") or [])],
        allow_writes=allow_writes,
        allow_process_control=bool(datos.get("allow_process_control", False)),
        max_output_bytes=int(datos.get("max_output_bytes", 100_000)),
        command_timeout=float(datos.get("command_timeout", 30.0)),
    )

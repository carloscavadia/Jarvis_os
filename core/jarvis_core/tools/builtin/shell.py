"""Herramienta: ejecutar comandos de shell (con lista blanca).

HERRAMIENTA SENSIBLE. El comando lo propone el modelo (entrada no confiable), así que:
  - Solo se permiten ejecutables de una lista blanca.
  - Se rechazan operadores de shell (|, &&, ;, `, $()...) para evitar cadenas peligrosas.
  - Hay timeout.
En producción, considera además ejecutar en un entorno aislado y pedir confirmación.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from jarvis_core.tools.base import Tool, ToolResult

_FORBIDDEN = {"|", "||", "&", "&&", ";", "`", "$(", ">", ">>", "<", "\n"}


class ShellTool(Tool):
    name = "run_shell"
    requires_confirmation = True

    def __init__(self, allowlist: list[str], timeout: float = 15.0) -> None:
        self.allowlist = set(allowlist)
        self.timeout = timeout
        self.description = (
            "Ejecuta un comando de shell en el servidor y devuelve su salida. "
            "Solo se permiten estos ejecutables: "
            + ", ".join(sorted(self.allowlist))
            + ". No se admiten tuberías ni encadenamiento de comandos."
        )

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Comando a ejecutar. Un único ejecutable con sus argumentos.",
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def run(self, command: str = "", **kwargs: Any) -> ToolResult:
        command = command.strip()
        if not command:
            return ToolResult(content="Comando vacío.", is_error=True)

        # Rechaza operadores de shell.
        if any(tok in command for tok in _FORBIDDEN):
            return ToolResult(
                content="Rechazado: no se permiten operadores de shell "
                "(tuberías, encadenamiento, redirecciones, etc.).",
                is_error=True,
            )

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return ToolResult(content=f"Comando mal formado: {exc}", is_error=True)

        if not parts:
            return ToolResult(content="Comando vacío.", is_error=True)

        executable = parts[0]
        if executable not in self.allowlist:
            return ToolResult(
                content=f"Rechazado: '{executable}' no está en la lista blanca.",
                is_error=True,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return ToolResult(content="El comando superó el tiempo límite.", is_error=True)
        except FileNotFoundError:
            return ToolResult(content=f"No se encontró '{executable}'.", is_error=True)

        output = stdout.decode(errors="replace").strip()
        if len(output) > 8000:
            output = output[:8000] + "\n… (salida truncada)"
        return ToolResult(
            content=output or "(sin salida)",
            is_error=proc.returncode != 0,
        )

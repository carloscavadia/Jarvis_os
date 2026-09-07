"""Herramienta: ejecutar comandos de shell.

HERRAMIENTA SENSIBLE. El comando lo propone el modelo (entrada no confiable).

La contención real la da `create_subprocess_exec`: el comando se parte con `shlex` y se
ejecuta **sin intérprete**, así que tuberías, encadenamientos y sustituciones no se
interpretan nunca —llegarían como argumentos literales del programa—. Antes había además
una lista de caracteres prohibidos (`|`, `&`, `>`…): no protegía de nada que `exec` no
cubriera ya, y en cambio rechazaba argumentos legítimos como una URL con `&` o una regex
con `|`. Se retiró por eso.

Quién puede ejecutar qué lo decide el motor de políticas (`jarvis_core.policy`), no esta
clase: la lista blanca se traduce a reglas ALLOW y lo que no está en ella pasa a ser una
confirmación en vez de un rechazo seco. `policy_subject` expone `executable` para que las
reglas se puedan escribir por programa.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from jarvis_core.tools.base import Tool, ToolResult


def split_command(command: str) -> list[str]:
    """Parte un comando en argv. Lanza `ValueError` si está mal formado."""
    return shlex.split(command)


class ShellTool(Tool):
    name = "run_shell"
    requires_confirmation = True
    policy_scope_key = "executable"

    def __init__(
        self,
        allowlist: list[str],
        timeout: float = 15.0,
        *,
        governed_by_policy: bool = False,
    ) -> None:
        self.allowlist = set(allowlist)
        self.timeout = timeout
        #: Cuando el motor de políticas gobierna la herramienta, un ejecutable fuera de
        #: la lista blanca ya ha pasado por una decisión explícita (ALLOW por regla, o
        #: ASK aprobada por el humano) antes de llegar aquí, así que no se vuelve a
        #: rechazar. Sin motor, la lista blanca sigue siendo un muro: fail-closed.
        self.governed_by_policy = governed_by_policy
        listado = ", ".join(sorted(self.allowlist))
        if governed_by_policy:
            self.description = (
                "Ejecuta un comando de shell en el servidor y devuelve su salida. "
                f"Estos ejecutables corren sin confirmación: {listado}. "
                "Cualquier otro se puede pedir, pero requiere aprobación del usuario. "
                "No se admiten tuberías ni encadenamiento: un único ejecutable con "
                "sus argumentos."
            )
        else:
            self.description = (
                "Ejecuta un comando de shell en el servidor y devuelve su salida. "
                f"Solo se permiten estos ejecutables: {listado}. "
                "No se admiten tuberías ni encadenamiento de comandos."
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

    def policy_subject(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Añade `executable` para que las reglas apunten al programa, no a la línea."""
        subject = dict(arguments)
        command = str(arguments.get("command", "")).strip()
        try:
            parts = split_command(command)
        except ValueError:
            parts = []
        subject["executable"] = parts[0] if parts else ""
        return subject

    def default_rules(self) -> list[Any]:
        """Reglas ALLOW derivadas de la lista blanca.

        Vive aquí y no en la configuración para que la lista blanca siga teniendo un
        único dueño: cambiarla en `Settings` cambia la política, sin duplicar nada.
        """
        from jarvis_core.policy.rules import Decision, Rule

        return [
            Rule(
                tool=self.name,
                decision=Decision.ALLOW,
                match={"executable": exe},
                reason="Ejecutable en la lista blanca.",
            )
            for exe in sorted(self.allowlist)
        ]

    async def run(self, command: str = "", **kwargs: Any) -> ToolResult:
        command = command.strip()
        if not command:
            return ToolResult(content="Comando vacío.", is_error=True)

        try:
            parts = split_command(command)
        except ValueError as exc:
            return ToolResult(content=f"Comando mal formado: {exc}", is_error=True)

        if not parts:
            return ToolResult(content="Comando vacío.", is_error=True)

        executable = parts[0]
        if not self.governed_by_policy and executable not in self.allowlist:
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
        except TimeoutError:
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

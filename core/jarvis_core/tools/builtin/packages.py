"""Instalación limitada de paquetes con confirmación obligatoria."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,126}[A-Za-z0-9]$")


class InstallPackageTool(Tool):
    name = "install_package"
    requires_confirmation = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "manager": {
                "type": "string",
                "enum": ["apt", "pip"],
                "description": "Gestor de paquetes autorizado por el administrador.",
            },
            "package": {
                "type": "string",
                "description": "Nombre exacto de un único paquete, sin opciones.",
            },
        },
        "required": ["manager", "package"],
        "additionalProperties": False,
    }

    def __init__(self, managers: list[str], timeout: float = 300.0) -> None:
        self.managers = {manager.lower() for manager in managers} & {"apt", "pip"}
        self.timeout = timeout
        self.description = (
            "Instala un único paquete con apt o pip. Siempre pide permiso explícito al "
            "usuario y no admite flags, comandos, URLs ni encadenamiento. Gestores activos: "
            + (", ".join(sorted(self.managers)) or "ninguno")
        )

    async def run(
        self,
        manager: str = "",
        package: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        manager = manager.lower().strip()
        package = package.strip()
        if manager not in self.managers:
            return ToolResult(
                content="Gestor de paquetes no autorizado.", is_error=True
            )
        if not _PACKAGE_RE.fullmatch(package):
            return ToolResult(
                content="Nombre de paquete inválido; no se permiten opciones ni URLs.",
                is_error=True,
            )

        if manager == "pip":
            command = [sys.executable, "-m", "pip", "install", "--user", package]
        elif os.geteuid() == 0:
            command = ["apt-get", "install", "-y", "--", package]
        else:
            command = ["sudo", "-n", "apt-get", "install", "-y", "--", package]

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return ToolResult(
                content="La instalación agotó el tiempo límite.", is_error=True
            )
        except FileNotFoundError:
            return ToolResult(
                content=(
                    "El instalador no está disponible o el servicio no tiene privilegios. "
                    "En Docker no se modifica el host; usa systemd y una regla sudo limitada."
                ),
                is_error=True,
            )

        output = stdout.decode(errors="replace").strip()
        if len(output) > 12000:
            output = output[-12000:] + "\n… (inicio de salida truncado)"
        return ToolResult(
            content=output or "Instalación terminada.", is_error=proc.returncode != 0
        )

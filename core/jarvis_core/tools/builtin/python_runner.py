"""Ejecución controlada de scripts Python guardados en el workspace."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any, ClassVar

from jarvis_core.tools.base import Tool, ToolResult
from jarvis_core.tools.builtin.filesystem import WorkspaceGuard


class RunPythonFileTool(Tool):
    name = "run_python_file"
    requires_confirmation = True
    description = (
        "Ejecuta un archivo .py existente dentro del workspace. Siempre solicita permiso "
        "humano, no acepta código inline ni comandos de shell, y limita tiempo y salida."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa al archivo .py."},
            "arguments": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
                "description": "Argumentos opcionales entregados al script.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        guard: WorkspaceGuard,
        *,
        timeout: float = 60.0,
        max_output_bytes: int = 12000,
    ) -> None:
        self.guard = guard
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def _resolve(self, raw_path: str, arguments: list[str]) -> tuple[Path, list[str]]:
        path = self.guard.resolve(raw_path)
        if path.suffix.lower() != ".py":
            raise ValueError("Solo se pueden ejecutar archivos con extensión .py.")
        if not path.is_file():
            raise ValueError("El script no existe dentro del workspace.")
        if path.stat().st_size > self.guard.max_file_bytes:
            raise ValueError("El script supera el límite de tamaño del workspace.")
        if len(arguments) > 20 or any(
            not isinstance(item, str) or len(item) > 1000 or "\0" in item
            for item in arguments
        ):
            raise ValueError("Los argumentos del script no son válidos.")
        return path, arguments

    async def run(
        self,
        path: str = "",
        arguments: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        script, safe_arguments = self._resolve(path, arguments or [])
        environment = {
            "HOME": os.environ.get("HOME", "/app"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            *safe_arguments,
            cwd=str(self.guard.root),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        async def collect_output() -> tuple[bytes, bool]:
            assert proc.stdout is not None
            captured = bytearray()
            truncated = False
            while chunk := await proc.stdout.read(4096):
                captured.extend(chunk)
                if len(captured) > self.max_output_bytes:
                    del captured[: len(captured) - self.max_output_bytes]
                    truncated = True
            await proc.wait()
            return bytes(captured), truncated

        try:
            stdout, truncated = await asyncio.wait_for(
                collect_output(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            return ToolResult(
                content=f"El script superó el límite de {self.timeout:g} segundos.",
                is_error=True,
            )

        output = stdout.decode(errors="replace").strip()
        if truncated:
            output = "… (inicio de salida truncado)\n" + output
        status = f"Código de salida: {proc.returncode}."
        return ToolResult(
            content=f"{status}\n{output}" if output else status,
            is_error=proc.returncode != 0,
        )

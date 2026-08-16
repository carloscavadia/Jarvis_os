"""Skill Manager: administra y ejecuta dinámicamente las habilidades aprendidas en JARVIS OS."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from typing import Any

from jarvis_core.skills.store import SkillRecord, SkillStore
from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.skills")

#: Se antepone al código de la habilidad para conservar su contrato original
#: —recibe `params`, deja el resultado en `result` u `output`— ahora que corre en
#: otro proceso y lo único que vuelve es la salida estándar.
_SKILL_PREAMBLE = """\
import atexit as _jarvis_atexit, os as _jarvis_os
params = _jarvis_os.environ.get("JARVIS_SKILL_PARAMS", "")
result = None
output = None


@_jarvis_atexit.register
def _jarvis_emit():
    valor = result if result is not None else output
    if valor is not None:
        print(valor)


"""


class DynamicSkillTool(Tool):
    """Herramienta de JARVIS OS adaptada desde una Habilidad Aprendida."""

    def __init__(self, record: SkillRecord, manager: SkillManager) -> None:
        self.name = f"skill_{record.name}".replace("-", "_").replace(".", "_")
        self.description = (
            f"[HABILIDAD APRENDIDA: {record.title or record.name}] "
            f"{record.description or 'Instrucción o script aprendido.'} (Activación: {record.trigger})"
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "params": {
                    "type": "string",
                    "description": "Parámetros o contexto adicional para la ejecución de la habilidad.",
                }
            },
        }
        self._record = record
        self._manager = manager
        # Una habilidad de instrucción sólo devuelve texto; una de python ejecuta
        # código, así que pasa por la misma puerta que `run_python_file`.
        self.requires_confirmation = record.skill_type == "python"

    async def run(self, params: str = "", **kwargs: Any) -> ToolResult:
        return await self._manager.execute_skill(self._record.name, params)


class SkillManager:
    """Orquestador de Habilidades y Auto-Aprendizaje para JARVIS OS."""

    def __init__(
        self,
        store: SkillStore,
        *,
        allow_python: bool = False,
        timeout: float = 60.0,
        max_output_bytes: int = 12000,
    ) -> None:
        self.store = store
        self.allow_python = allow_python
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self._active_tools: dict[str, DynamicSkillTool] = {}
        self.sync_tools()

    def sync_tools(self) -> list[Tool]:
        """Sincroniza y reconstruye la lista de herramientas dinámicas a partir del store."""
        self._active_tools.clear()
        records = self.store.list_all()
        for record in records:
            if record.enabled:
                tool = DynamicSkillTool(record, self)
                self._active_tools[tool.name] = tool
        return list(self._active_tools.values())

    def get_registered_tools(self) -> list[Tool]:
        """Devuelve las herramientas de habilidades listas para registrar en el ToolRegistry."""
        return list(self._active_tools.values())

    async def execute_skill(self, name: str, params: str = "") -> ToolResult:
        """Ejecuta una habilidad aprendida por nombre."""
        record = self.store.get(name)
        if not record or not record.enabled:
            return ToolResult(f"La habilidad '{name}' no existe o está pausada.", is_error=True)

        logger.info("Ejecutando Habilidad Aprendida '%s' (tipo: %s)", name, record.skill_type)

        try:
            if record.skill_type == "python":
                if not self.allow_python:
                    return ToolResult(
                        f"La habilidad '{name}' contiene código Python y la ejecución "
                        "de habilidades Python está desactivada. Actívala con "
                        "JARVIS_SKILLS_PYTHON_ENABLED=true si de verdad quieres que "
                        "JARVIS ejecute código que ha escrito él mismo.",
                        is_error=True,
                    )
                output = await self._run_python_skill(record.content, params)
                self.store.increment_usage(name, success=True)
                return ToolResult(output)
            else:
                # Devuelve el procedimiento/instrucción aprendida
                self.store.increment_usage(name, success=True)
                res_text = (
                    f"### [HABILIDAD APRENDIDA: {record.title or record.name}]\n"
                    f"{record.content}\n\n"
                    f"**Contexto recibido**: {params or 'Ninguno'}"
                )
                return ToolResult(res_text)

        except Exception as exc:
            logger.error("Error al ejecutar habilidad '%s': %s", name, exc)
            self.store.increment_usage(name, success=False)
            return ToolResult(f"Error ejecutando habilidad '{name}': {exc}", is_error=True)

    async def _run_python_skill(self, code: str, params: str) -> str:
        """Ejecuta el código de una habilidad en un proceso aparte.

        En proceso, un `exec()` compartiría memoria con el gateway: las claves ya
        cargadas, el almacén de conectores y el propio bucle de eventos quedarían
        al alcance del código, y un bucle infinito colgaría el servidor entero.
        Un subproceso con tiempo límite es lo que ya hace `run_python_file`, y
        esta ruta no tiene motivo para ser más permisiva que aquella.
        """
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",  # aislado: ignora PYTHON* y el directorio del script
            "-c",
            _SKILL_PREAMBLE + code,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={"JARVIS_SKILL_PARAMS": params, "PATH": os.environ.get("PATH", "")},
            cwd=tempfile.gettempdir(),
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"La habilidad superó el límite de {self.timeout:.0f} s y se detuvo."
            ) from None

        output = raw.decode("utf-8", errors="replace")[: self.max_output_bytes]
        if proc.returncode != 0:
            raise RuntimeError(output.strip() or f"salió con código {proc.returncode}")
        return output.strip() or "Habilidad Python ejecutada."

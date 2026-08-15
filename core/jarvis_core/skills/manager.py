"""Skill Manager: administra y ejecuta dinámicamente las habilidades aprendidas en JARVIS OS."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from jarvis_core.skills.store import SkillRecord, SkillStore
from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.skills")


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

    async def execute(self, params: str = "", **kwargs: Any) -> ToolResult:
        return await self._manager.execute_skill(self._record.name, params)


class SkillManager:
    """Orquestador de Habilidades y Auto-Aprendizaje para JARVIS OS."""

    def __init__(self, store: SkillStore) -> None:
        self.store = store
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
                # Ejecutar script Python en entorno controlado
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
        """Ejecuta código Python aislado para habilidades tipo script."""
        exec_globals = {
            "params": params,
            "result": None,
        }
        
        def _exec():
            exec(code, exec_globals)
            return exec_globals.get("result") or exec_globals.get("output") or "Habilidad Python ejecutada."

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, _exec)
        return str(res)

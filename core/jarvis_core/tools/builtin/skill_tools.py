"""Herramientas agénticas para el Sistema de Habilidades y Auto-Aprendizaje de JARVIS OS."""

from __future__ import annotations

from typing import Any

from jarvis_core.skills.learning_engine import SelfLearningEngine
from jarvis_core.skills.manager import SkillManager
from jarvis_core.skills.store import SkillStore
from jarvis_core.tools.base import Tool, ToolResult


class LearnSkillTool(Tool):
    """Herramienta para que JARVIS guarde y aprenda una nueva Habilidad de forma autónoma."""

    name = "learn_skill"
    description = (
        "Registra y aprende una nueva Habilidad permanente (guía de procedimiento o script Python). "
        "Permite a JARVIS recordar cómo realizar tareas complejas en el futuro."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Identificador único en minúsculas y sin espacios (ej: format_json_report)."},
            "title": {"type": "string", "description": "Título descriptivo de la habilidad."},
            "trigger": {"type": "string", "description": "Palabras o frases clave de activación (ej: 'reporte json', 'formatear reporte')."},
            "description": {"type": "string", "description": "Resumen de lo que hace la habilidad."},
            "content": {"type": "string", "description": "Instrucciones detalladas en Markdown o código ejecutable en Python."},
            "skill_type": {"type": "string", "enum": ["instruction", "python"], "description": "Tipo de habilidad: 'instruction' o 'python'."},
        },
        "required": ["name", "title", "trigger", "description", "content"],
    }

    def __init__(self, engine: SelfLearningEngine) -> None:
        self.engine = engine

    async def execute(
        self,
        name: str,
        title: str,
        trigger: str,
        description: str,
        content: str,
        skill_type: str = "instruction",
        **kwargs: Any,
    ) -> ToolResult:
        try:
            record = self.engine.learn_new_skill(
                name=name,
                title=title,
                trigger=trigger,
                description=description,
                content=content,
                skill_type=skill_type,
            )
            return ToolResult(
                f"✓ Habilidad '{record.name}' aprendida con éxito.\n"
                f"Trigger: {record.trigger}\n"
                f"Tipo: {record.skill_type.upper()}"
            )
        except Exception as exc:
            return ToolResult(f"Error al aprender habilidad: {exc}", is_error=True)


class ListSkillsTool(Tool):
    """Consulta la lista de habilidades aprendidas por JARVIS."""

    name = "list_skills"
    description = "Devuelve la lista de todas las Habilidades aprendidas por JARVIS y sus palabras de activación."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, store: SkillStore) -> None:
        self.store = store

    async def execute(self, **kwargs: Any) -> ToolResult:
        skills = self.store.list_all()
        if not skills:
            return ToolResult("Aún no hay Habilidades aprendidas registradas.")
        lines = ["### 🧠 HABILIDADES APRENDIDAS POR JARVIS:"]
        for s in skills:
            status = "● ACTIVA" if s.enabled else "○ PAUSADA"
            lines.append(
                f"- **{s.name}** ({s.skill_type.upper()}) [{status}] · {s.title}\n"
                f"  *Trigger*: `{s.trigger}` | *Usos*: {s.usage_count}\n"
                f"  *Descripción*: {s.description}"
            )
        return ToolResult("\n".join(lines))


class ExecuteSkillTool(Tool):
    """Ejecuta una habilidad aprendida por nombre."""

    name = "execute_skill"
    description = "Ejecuta una Habilidad aprendida previamente utilizando su nombre corto."
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nombre exacto de la habilidad a ejecutar."},
            "params": {"type": "string", "description": "Parámetros o datos de entrada para la habilidad."},
        },
        "required": ["name"],
    }

    def __init__(self, manager: SkillManager) -> None:
        self.manager = manager

    async def execute(self, name: str, params: str = "", **kwargs: Any) -> ToolResult:
        return await self.manager.execute_skill(name, params)

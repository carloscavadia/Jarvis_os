"""Motor de Auto-Aprendizaje Autónomo (Self-Learning Engine) para JARVIS OS."""

from __future__ import annotations

import logging
from typing import Any

from jarvis_core.skills.manager import SkillManager
from jarvis_core.skills.store import SkillRecord, SkillStore

logger = logging.getLogger("jarvis.skills.learning")


class SelfLearningEngine:
    """Extrae, sintetiza y guarda automáticamente nuevas habilidades a partir de interacciones."""

    def __init__(self, store: SkillStore, manager: SkillManager) -> None:
        self.store = store
        self.manager = manager

    def learn_new_skill(
        self,
        name: str,
        title: str,
        trigger: str,
        description: str,
        content: str,
        skill_type: str = "instruction",
    ) -> SkillRecord:
        """Registra explícitamente una nueva Habilidad aprendida."""
        clean_name = (
            name.lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace(".", "_")
            .strip()
        )
        if not clean_name:
            raise ValueError("El nombre de la habilidad no puede estar vacío.")

        record = SkillRecord(
            name=clean_name,
            title=title or clean_name,
            trigger=trigger,
            description=description,
            skill_type=skill_type if skill_type in {"python", "instruction"} else "instruction",
            content=content,
            enabled=True,
        )

        self.store.save(record)
        self.manager.sync_tools()
        logger.info("NUEVA HABILIDAD APRENDIDA: '%s' (Trigger: %s)", clean_name, trigger)
        return record

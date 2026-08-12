"""Gestión de sesiones de conversación.

Cada cliente (una app, un dispositivo ESP32, una pestaña web) tiene su propia sesión con
su propio orquestador, de modo que las conversaciones no se mezclan. Comparten la misma
configuración, memoria interna, herramientas y almacén de tareas.
"""

from __future__ import annotations

import asyncio

from jarvis_core.agent.emotion import EmotionState
from jarvis_core.agent.orchestrator import Orchestrator
from jarvis_core.config import Settings
from jarvis_core.llm.factory import build_llm
from jarvis_core.memory.store import MemoryStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.builtin import build_default_registry


class SessionManager:
    """Crea y cachea orquestadores por `session_id`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Memoria y tareas son compartidas por todas las sesiones (son "de JARVIS").
        self.memory = MemoryStore(settings.memory_db_path)
        self.tasks = TaskStore(settings.tasks_db_path)
        self._sessions: dict[str, Orchestrator] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> Orchestrator:
        async with self._lock:
            orch = self._sessions.get(session_id)
            if orch is None:
                if len(self._sessions) >= self._settings.gateway_max_sessions:
                    raise RuntimeError("Se alcanzó el límite de sesiones activas.")
                llm = build_llm(self._settings)
                emotion = EmotionState()
                # El gateway es un canal remoto: no expone shell. La CLI local puede
                # seguir habilitándolo mediante JARVIS_ENABLE_SHELL.
                registry = build_default_registry(
                    self._settings,
                    self.memory,
                    self.tasks,
                    emotion,
                    allow_shell=False,
                )
                orch = Orchestrator(llm, registry, self._settings, emotion=emotion)
                self._sessions[session_id] = orch
            return orch

    def reset(self, session_id: str) -> None:
        orch = self._sessions.get(session_id)
        if orch is not None:
            orch.reset()

    def close(self) -> None:
        self.memory.close()
        self.tasks.close()

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
from jarvis_core.connectors.events import ProactiveEventStore
from jarvis_core.connectors.store import ConnectorStore
from jarvis_core.goals.store import GoalStore
from jarvis_core.llm.factory import build_llm
from jarvis_core.mcp import MCPManager, MCPStore
from jarvis_core.memory.store import MemoryStore
from jarvis_core.calendar.store import CalendarStore
from jarvis_core.memory.embeddings import build_embedder
from jarvis_core.skills import SkillManager, SkillStore
from jarvis_core.tasks.store import TaskStore
from jarvis_core.tools.base import ToolRegistry
from jarvis_core.tools.builtin import build_default_registry


class SessionManager:
    """Crea y cachea orquestadores por `session_id`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Memoria y tareas son compartidas por todas las sesiones (son "de JARVIS").
        self.memory = MemoryStore(settings.memory_db_path, embedder=build_embedder(settings))
        self.calendar = CalendarStore(settings.calendar_db_path)
        self.tasks = TaskStore(settings.tasks_db_path)
        self.goals = GoalStore(settings.goals_db_path)
        self.proactive_events = ProactiveEventStore(settings.proactive_events_db_path)
        self.mcp_store = MCPStore(settings.mcp_db_path)
        self.mcp_manager = MCPManager(self.mcp_store)
        self.skill_store = SkillStore(settings.skills_db_path)
        self.skill_manager = SkillManager(
            self.skill_store, allow_python=getattr(settings, "skills_python_enabled", True)
        )
        self._sessions: dict[str, Orchestrator] = {}
        # Se conserva el estado emocional de cada sesión para poder rehacer sus
        # herramientas sin perder el color que JARVIS tenga en ese momento.
        self._emotions: dict[str, EmotionState] = {}
        self.connector_store = (
            ConnectorStore(settings.connector_db_path, settings.connector_master_key)
            if settings.connectors_enabled and settings.connector_master_key
            else None
        )
        self._lock = asyncio.Lock()
        self._persona_overlay = self._settings.persona_extra.strip()

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
                    connector_store=self.connector_store,
                    goals=self.goals,
                    mcp_manager=self.mcp_manager,
                    skill_manager=self.skill_manager,
                    calendar=self.calendar,
                    llm=llm,
                )
                orch = Orchestrator(
                    llm, registry, self._settings, emotion=emotion, memory=self.memory
                )
                orch.set_persona(self._persona_overlay)
                self._sessions[session_id] = orch
                self._emotions[session_id] = emotion
            return orch

    async def refresh_tools(self) -> None:
        """Reconstruye las herramientas de las sesiones ya abiertas.

        Al registrar un conector, las herramientas que dependen de él solo
        existirían en sesiones nuevas. Sin esto, quien acaba de registrar su
        servidor de música seguiría oyendo «no puedo reproducir audio» hasta
        reiniciar el gateway. La conversación se conserva.
        """
        async with self._lock:
            for session_id, orch in self._sessions.items():
                orch.set_registry(
                    self.build_registry(
                        self._emotions.setdefault(session_id, EmotionState())
                    )
                )

    async def set_persona(self, overlay: str) -> None:
        """Aplica reglas de la casa a todas las sesiones, abiertas y futuras.

        El prompt base se congela al construir cada orquestador, así que hasta
        ahora cambiar el tono exigía reiniciar el gateway. Esto viaja por la capa
        volátil, de modo que tampoco invalida el prefijo cacheado.
        """
        self._persona_overlay = overlay.strip()
        async with self._lock:
            for orch in self._sessions.values():
                orch.set_persona(self._persona_overlay)

    @property
    def persona(self) -> str:
        return self._persona_overlay

    def build_registry(self, emotion: EmotionState) -> ToolRegistry:
        """Herramientas para un canal que no usa el orquestador (voz Realtime).

        Comparte memoria, tareas, objetivos y conectores con las sesiones de texto:
        lo que JARVIS aprenda hablando sigue ahí cuando escribas, y al revés.
        """
        return build_default_registry(
            self._settings,
            self.memory,
            self.tasks,
            emotion,
            allow_shell=False,
            connector_store=self.connector_store,
            goals=self.goals,
            mcp_manager=self.mcp_manager,
            skill_manager=self.skill_manager,
            calendar=self.calendar,
        )

    def reset(self, session_id: str) -> None:
        orch = self._sessions.get(session_id)
        if orch is not None:
            orch.reset()

    def close(self) -> None:
        self.memory.close()
        self.tasks.close()
        self.goals.close()
        self.proactive_events.close()
        if self.connector_store is not None:
            self.connector_store.close()

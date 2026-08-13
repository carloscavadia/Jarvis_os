"""Credenciales efímeras para voz OpenAI Realtime bajo demanda."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_core.config import Settings


class RealtimeVoiceError(RuntimeError):
    pass


class RealtimeVoiceBroker:
    """Crea sesiones breves sin exponer la API key permanente al navegador."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self.settings = settings
        self._client = client
        self._session_lock = asyncio.Lock()
        self._prepare_db()

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.openai_realtime_enabled
            and self.settings.openai_responses_api_key
        )

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _prepare_db(self) -> None:
        path = Path(self.settings.openai_usage_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as database:
            database.execute(
                """CREATE TABLE IF NOT EXISTS realtime_daily_sessions (
                       day TEXT PRIMARY KEY,
                       sessions INTEGER NOT NULL DEFAULT 0
                   )"""
            )

    def sessions_today(self) -> int:
        with sqlite3.connect(self.settings.openai_usage_db_path) as database:
            row = database.execute(
                "SELECT sessions FROM realtime_daily_sessions WHERE day = ?",
                (self._today(),),
            ).fetchone()
        return int(row[0]) if row else 0

    def _record_session(self) -> None:
        with sqlite3.connect(self.settings.openai_usage_db_path) as database:
            database.execute(
                """INSERT INTO realtime_daily_sessions(day, sessions) VALUES (?, 1)
                   ON CONFLICT(day) DO UPDATE SET sessions = sessions + 1""",
                (self._today(),),
            )

    def budget_available(self) -> bool:
        limit = self.settings.openai_realtime_daily_sessions
        return not limit or self.sessions_today() < limit

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled and self.budget_available(),
            "configured": self.enabled,
            "model": self.settings.openai_realtime_model,
            "voice": self.settings.openai_realtime_voice,
            "sessions_today": self.sessions_today(),
            "daily_session_limit": self.settings.openai_realtime_daily_sessions,
            "input_mode": "local_text_only",
        }

    async def create_client_secret(self) -> dict[str, object]:
        if not self.enabled:
            raise RealtimeVoiceError("La voz OpenAI Realtime no está configurada.")
        async with self._session_lock:
            if not self.budget_available():
                raise RealtimeVoiceError(
                    "Se alcanzó el límite diario de sesiones de voz Realtime."
                )
            if self._client is None:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    api_key=self.settings.openai_responses_api_key
                )

            secret = await self._client.realtime.client_secrets.create(
                expires_after={
                    "anchor": "created_at",
                    "seconds": self.settings.openai_realtime_session_seconds,
                },
                session={
                    "type": "realtime",
                    "model": self.settings.openai_realtime_model,
                    "output_modalities": ["audio"],
                    "max_output_tokens": self.settings.openai_realtime_max_output_tokens,
                    "instructions": (
                        "Habla siempre en español. Eres la voz de JARVIS: masculina, "
                        "serena, precisa y natural. Cuando recibas texto para leer, "
                        "pronúncialo fielmente, sin añadir comentarios ni saludos."
                    ),
                    "audio": {
                        "output": {"voice": self.settings.openai_realtime_voice}
                    },
                },
            )
            self._record_session()
        return {
            "value": secret.value,
            "expires_at": secret.expires_at,
            "model": self.settings.openai_realtime_model,
            "max_session_seconds": self.settings.openai_realtime_session_seconds,
        }

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
            # El audio es lo que cuesta, y hablar cuesta el doble por token que
            # escuchar. Guardarlo separado permite ver de dónde sale la factura.
            database.execute(
                """CREATE TABLE IF NOT EXISTS realtime_daily_usage (
                       day TEXT PRIMARY KEY,
                       input_tokens INTEGER NOT NULL DEFAULT 0,
                       output_tokens INTEGER NOT NULL DEFAULT 0,
                       input_audio_tokens INTEGER NOT NULL DEFAULT 0,
                       output_audio_tokens INTEGER NOT NULL DEFAULT 0,
                       cached_tokens INTEGER NOT NULL DEFAULT 0
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

    def record_session(self) -> None:
        """Cuenta una conversación abierta desde el gateway."""
        self._record_session()

    def usage_today(self) -> dict[str, int]:
        columns = (
            "input_tokens",
            "output_tokens",
            "input_audio_tokens",
            "output_audio_tokens",
            "cached_tokens",
        )
        with sqlite3.connect(self.settings.openai_usage_db_path) as database:
            row = database.execute(
                f"SELECT {', '.join(columns)} FROM realtime_daily_usage WHERE day = ?",
                (self._today(),),
            ).fetchone()
        return dict(zip(columns, row, strict=True)) if row else dict.fromkeys(columns, 0)

    def record_usage(self, usage: dict[str, int]) -> None:
        """Acumula el consumo de una conversación ya terminada."""
        if not any(usage.values()):
            return
        with sqlite3.connect(self.settings.openai_usage_db_path) as database:
            database.execute(
                """INSERT INTO realtime_daily_usage(
                       day, input_tokens, output_tokens,
                       input_audio_tokens, output_audio_tokens, cached_tokens
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(day) DO UPDATE SET
                     input_tokens = input_tokens + excluded.input_tokens,
                     output_tokens = output_tokens + excluded.output_tokens,
                     input_audio_tokens =
                       input_audio_tokens + excluded.input_audio_tokens,
                     output_audio_tokens =
                       output_audio_tokens + excluded.output_audio_tokens,
                     cached_tokens = cached_tokens + excluded.cached_tokens""",
                (
                    self._today(),
                    max(0, int(usage.get("input_tokens", 0))),
                    max(0, int(usage.get("output_tokens", 0))),
                    max(0, int(usage.get("input_audio_tokens", 0))),
                    max(0, int(usage.get("output_audio_tokens", 0))),
                    max(0, int(usage.get("cached_tokens", 0))),
                ),
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
            "input_mode": (
                "local_audio_streamed"
                if self.settings.realtime_conversation_enabled
                else "local_text_only"
            ),
            "conversation": self.settings.realtime_conversation_enabled and self.enabled,
            "usage_today": self.usage_today(),
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

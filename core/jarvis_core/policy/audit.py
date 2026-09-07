"""Registro de auditoría: qué hizo el agente, cuándo y con qué permiso.

El log operativo (`logger.info`) deja fuera los argumentos a propósito, porque pueden
llevar correos o secretos. La consecuencia es que hoy nadie puede responder "¿qué tocó
JARVIS en mi máquina el martes?". Esto es el otro destino: append-only, en su propia
base, con los argumentos **redactados** en vez de omitidos.

Se guardan dos cosas por llamada:

- `args_digest` — sha256 del JSON canónico. Correlaciona llamadas idénticas y detecta
  manipulación posterior sin exponer nada.
- `args_preview` — los argumentos con las claves sensibles enmascaradas y recortadas.
  Un log que solo tiene hashes es auditable pero no legible, y uno que nadie lee no
  sirve para vigilar a un agente.

No hay `update` ni `delete`: la clase no expone forma de reescribir la historia.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

#: Claves cuyo valor nunca se escribe en claro. Se comparan en minúsculas y por
#: subcadena, así que `api_key`, `AUTH_TOKEN` o `db_password` quedan cubiertas.
_SECRET_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

_MAX_PREVIEW_CHARS = 500


def _redact(arguments: dict[str, Any]) -> dict[str, Any]:
    """Enmascara valores sensibles antes de que toquen el disco."""
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered = key.lower()
        if any(hint in lowered for hint in _SECRET_HINTS):
            safe[key] = "***"
        elif isinstance(value, str) and len(value) > _MAX_PREVIEW_CHARS:
            safe[key] = value[:_MAX_PREVIEW_CHARS] + "…"
        else:
            safe[key] = value
    return safe


def digest_arguments(arguments: dict[str, Any]) -> str:
    """sha256 del JSON canónico de los argumentos (claves ordenadas)."""
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditEntry:
    id: int
    created_at: float
    session: str
    tool: str
    decision: str
    reason: str
    args_digest: str
    args_preview: str
    outcome: str
    duration_ms: int


class AuditLog:
    """Bitácora append-only de decisiones y ejecuciones de herramientas."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   REAL NOT NULL,
                    session      TEXT NOT NULL DEFAULT '',
                    tool         TEXT NOT NULL,
                    decision     TEXT NOT NULL,
                    reason       TEXT NOT NULL DEFAULT '',
                    args_digest  TEXT NOT NULL,
                    args_preview TEXT NOT NULL DEFAULT '',
                    outcome      TEXT NOT NULL DEFAULT '',
                    duration_ms  INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Las consultas útiles son "qué pasó últimamente" y "qué hizo esta
            # herramienta"; sin índice, ambas degradan a escaneo completo en cuanto
            # el registro crece, que es justo cuando hace falta consultarlo.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit(tool, created_at DESC)"
            )
            self._conn.commit()

    def record(
        self,
        *,
        tool: str,
        decision: str,
        arguments: dict[str, Any] | None = None,
        session: str = "",
        reason: str = "",
        outcome: str = "",
        duration_ms: int = 0,
    ) -> int:
        """Escribe una entrada y devuelve su id."""
        arguments = arguments or {}
        preview = json.dumps(_redact(arguments), ensure_ascii=False, default=str)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO audit (
                    created_at, session, tool, decision, reason,
                    args_digest, args_preview, outcome, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    session,
                    tool,
                    decision,
                    reason,
                    digest_arguments(arguments),
                    preview[:2000],
                    outcome,
                    int(duration_ms),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def tail(self, limit: int = 50, tool: str | None = None) -> list[AuditEntry]:
        """Últimas entradas, de la más reciente a la más antigua."""
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            if tool:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE tool = ? ORDER BY created_at DESC LIMIT ?",
                    (tool, safe_limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
        return [
            AuditEntry(
                id=r["id"],
                created_at=r["created_at"],
                session=r["session"],
                tool=r["tool"],
                decision=r["decision"],
                reason=r["reason"],
                args_digest=r["args_digest"],
                args_preview=r["args_preview"],
                outcome=r["outcome"],
                duration_ms=r["duration_ms"],
            )
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

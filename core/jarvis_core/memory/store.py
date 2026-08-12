"""Memoria a largo plazo respaldada por SQLite y segura entre hilos."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass


@dataclass
class Memory:
    id: int
    key: str
    value: str
    tags: str
    created_at: float


class MemoryStore:
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
                CREATE TABLE IF NOT EXISTS memories (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    key        TEXT NOT NULL UNIQUE,
                    value      TEXT NOT NULL,
                    tags       TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            # Repositorios anteriores pueden no tener la restricción UNIQUE. Este índice
            # evita nuevas claves duplicadas sin exigir una migración destructiva.
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_key ON memories(key)"
            )
            self._conn.commit()

    def remember(self, key: str, value: str, tags: str = "") -> int:
        """Guarda o actualiza un hecho de manera atómica."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memories (key, value, tags, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    tags = excluded.tags,
                    created_at = excluded.created_at
                """,
                (key, value, tags, now),
            )
            row = self._conn.execute(
                "SELECT id FROM memories WHERE key = ?", (key,)
            ).fetchone()
            self._conn.commit()
            return int(row["id"])

    def recall(self, query: str = "", limit: int = 10) -> list[Memory]:
        """Recupera hechos. Si hay `query`, busca en clave/valor/etiquetas."""
        safe_limit = max(1, min(int(limit), 100))
        with self._lock:
            if query:
                like = f"%{query}%"
                rows = self._conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (like, like, like, safe_limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
        return [
            Memory(
                id=r["id"],
                key=r["key"],
                value=r["value"],
                tags=r["tags"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def forget(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

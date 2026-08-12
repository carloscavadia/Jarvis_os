"""Memoria a largo plazo respaldada por SQLite.

Versión mínima: almacén clave/hecho con etiquetas y búsqueda por texto. En una fase
posterior se añadirá búsqueda semántica con embeddings (sqlite-vec / pgvector).
"""

from __future__ import annotations

import os
import sqlite3
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
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                tags       TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def remember(self, key: str, value: str, tags: str = "") -> int:
        """Guarda o actualiza un hecho. Si la clave existe, se sobrescribe."""
        cur = self._conn.execute("SELECT id FROM memories WHERE key = ?", (key,))
        row = cur.fetchone()
        now = time.time()
        if row:
            self._conn.execute(
                "UPDATE memories SET value = ?, tags = ?, created_at = ? WHERE id = ?",
                (value, tags, now, row["id"]),
            )
            self._conn.commit()
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO memories (key, value, tags, created_at) VALUES (?, ?, ?, ?)",
            (key, value, tags, now),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def recall(self, query: str = "", limit: int = 10) -> list[Memory]:
        """Recupera hechos. Si hay `query`, busca en clave/valor/etiquetas."""
        if query:
            like = f"%{query}%"
            cur = self._conn.execute(
                """
                SELECT * FROM memories
                WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (like, like, like, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [
            Memory(
                id=r["id"], key=r["key"], value=r["value"],
                tags=r["tags"], created_at=r["created_at"],
            )
            for r in cur.fetchall()
        ]

    def forget(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()

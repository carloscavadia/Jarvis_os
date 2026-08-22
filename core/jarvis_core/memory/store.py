"""Memoria a largo plazo respaldada por SQLite y segura entre hilos."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

from jarvis_core.memory.embeddings import (
    Embedder,
    cosine_similarity,
    pack_vector,
    unpack_vector,
)


@dataclass
class Memory:
    id: int
    key: str
    value: str
    tags: str
    created_at: float


class MemoryStore:
    def __init__(self, db_path: str, embedder: Embedder | None = None) -> None:
        self.db_path = db_path
        #: Opcional de principio a fin: sin él la memoria busca por texto.
        self.embedder = embedder
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
            # La columna de vectores se añade sobre bases ya existentes sin
            # tocar los datos: los hechos guardados antes se quedan sin vector
            # y se les calcula la primera vez que se busque por significado.
            columnas = {
                fila["name"]
                for fila in self._conn.execute("PRAGMA table_info(memories)")
            }
            if "embedding" not in columnas:
                self._conn.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
            self._conn.commit()

    def _embed(self, text: str) -> bytes | None:
        if self.embedder is None:
            return None
        vector = self.embedder.embed(text)
        return pack_vector(vector) if vector else None

    def remember(self, key: str, value: str, tags: str = "") -> int:
        """Guarda o actualiza un hecho de manera atómica."""
        now = time.time()
        vector = self._embed(f"{key}: {value} {tags}".strip())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memories (key, value, tags, created_at, embedding)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    tags = excluded.tags,
                    created_at = excluded.created_at,
                    embedding = excluded.embedding
                """,
                (key, value, tags, now, vector),
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

    def _backfill_embeddings(self, limit: int = 200) -> None:
        """Calcula los vectores que falten, poco a poco.

        Los hechos guardados antes de activar la memoria semántica no tienen
        vector. Se calculan la primera vez que hacen falta, a ritmo acotado
        para que activar la función no congele el primer turno.
        """
        if self.embedder is None:
            return
        with self._lock:
            pendientes = self._conn.execute(
                "SELECT id, key, value, tags FROM memories WHERE embedding IS NULL LIMIT ?",
                (limit,),
            ).fetchall()
        for fila in pendientes:
            vector = self._embed(f"{fila['key']}: {fila['value']} {fila['tags']}".strip())
            if vector is None:
                return  # el embedder no está dando resultados; no insistir
            with self._lock:
                self._conn.execute(
                    "UPDATE memories SET embedding = ? WHERE id = ?", (vector, fila["id"])
                )
                self._conn.commit()

    def recall_semantic(
        self, query: str, limit: int = 10, min_similarity: float = 0.35
    ) -> list[Memory]:
        """Recupera hechos por **significado**, no por coincidencia de texto.

        Guardar «mi coche es un Tesla Model 3 azul» y preguntar «¿cuánto tarda
        en cargar el vehículo?» no compartía ni una palabra, así que la búsqueda
        por texto no devolvía nada. Aquí sí.

        Si no hay embedder, cae en `recall`: la memoria nunca deja de funcionar
        por no tener el modelo.
        """
        if self.embedder is None or not query.strip():
            return self.recall(query, limit)
        vector_consulta = self.embedder.embed(query)
        if not vector_consulta:
            return self.recall(query, limit)

        self._backfill_embeddings()
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM memories WHERE embedding IS NOT NULL"
            ).fetchall()
        if not filas:
            return self.recall(query, limit)

        puntuados = []
        for fila in filas:
            similitud = cosine_similarity(vector_consulta, unpack_vector(fila["embedding"]))
            if similitud >= min_similarity:
                puntuados.append((similitud, fila))
        puntuados.sort(key=lambda par: par[0], reverse=True)

        return [
            Memory(
                id=fila["id"],
                key=fila["key"],
                value=fila["value"],
                tags=fila["tags"],
                created_at=fila["created_at"],
            )
            for _, fila in puntuados[: max(1, min(int(limit), 100))]
        ]

    def forget(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

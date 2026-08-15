"""Persistencia SQLite para el Sistema de Habilidades (Skills) de JARVIS OS."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillRecord:
    name: str
    title: str = ""
    trigger: str = ""
    description: str = ""
    skill_type: str = "instruction"  # "instruction" | "python"
    content: str = ""
    enabled: bool = True
    usage_count: int = 0
    success_rate: float = 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillRecord:
        return cls(
            name=str(data.get("name", "")),
            title=str(data.get("title", "")),
            trigger=str(data.get("trigger", "")),
            description=str(data.get("description", "")),
            skill_type=str(data.get("skill_type", "instruction")),
            content=str(data.get("content", "")),
            enabled=bool(data.get("enabled", True)),
            usage_count=int(data.get("usage_count", 0)),
            success_rate=float(data.get("success_rate", 1.0)),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class SkillStore:
    """Administra la base de datos de Habilidades aprendidas por JARVIS."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    name TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    trigger TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    skill_type TEXT NOT NULL DEFAULT 'instruction',
                    content TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    success_rate REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.commit()

    def list_all(self) -> list[SkillRecord]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM skills ORDER BY updated_at DESC").fetchall()
            results = []
            for row in rows:
                results.append(
                    SkillRecord(
                        name=row["name"],
                        title=row["title"],
                        trigger=row["trigger"],
                        description=row["description"],
                        skill_type=row["skill_type"],
                        content=row["content"],
                        enabled=bool(row["enabled"]),
                        usage_count=int(row["usage_count"]),
                        success_rate=float(row["success_rate"]),
                        created_at=float(row["created_at"]),
                        updated_at=float(row["updated_at"]),
                    )
                )
            return results

    def get(self, name: str) -> SkillRecord | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
            if row is None:
                return None
            return SkillRecord(
                name=row["name"],
                title=row["title"],
                trigger=row["trigger"],
                description=row["description"],
                skill_type=row["skill_type"],
                content=row["content"],
                enabled=bool(row["enabled"]),
                usage_count=int(row["usage_count"]),
                success_rate=float(row["success_rate"]),
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
            )

    def save(self, record: SkillRecord) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO skills (name, title, trigger, description, skill_type, content, enabled, usage_count, success_rate, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    title = excluded.title,
                    trigger = excluded.trigger,
                    description = excluded.description,
                    skill_type = excluded.skill_type,
                    content = excluded.content,
                    enabled = excluded.enabled,
                    usage_count = excluded.usage_count,
                    success_rate = excluded.success_rate,
                    updated_at = excluded.updated_at;
                """,
                (
                    record.name,
                    record.title or record.name,
                    record.trigger,
                    record.description,
                    record.skill_type,
                    record.content,
                    1 if record.enabled else 0,
                    record.usage_count,
                    record.success_rate,
                    record.created_at,
                    now,
                ),
            )
            conn.commit()

    def increment_usage(self, name: str, success: bool = True) -> None:
        record = self.get(name)
        if not record:
            return
        new_count = record.usage_count + 1
        # Media móvil ponderada de éxito
        alpha = 0.2
        new_success_rate = record.success_rate * (1 - alpha) + (1.0 if success else 0.0) * alpha
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE skills SET usage_count = ?, success_rate = ?, updated_at = ? WHERE name = ?",
                (new_count, new_success_rate, time.time(), name),
            )
            conn.commit()

    def delete(self, name: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM skills WHERE name = ?", (name,))
            conn.commit()
            return cursor.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE skills SET enabled = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, time.time(), name),
            )
            conn.commit()
            return cursor.rowcount > 0

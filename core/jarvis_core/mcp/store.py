"""Almacenamiento SQLite persistente para servidores MCP de JARVIS OS."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPServerRecord:
    name: str
    transport: str = "stdio"  # "stdio" | "sse"
    command: str = ""  # e.g. "npx"
    args: list[str] = field(default_factory=list)  # e.g. ["-y", "@modelcontextprotocol/server-github"]
    env: dict[str, str] = field(default_factory=dict)  # e.g. {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}
    url: str = ""  # e.g. "http://localhost:8000/sse" for transport="sse"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServerRecord:
        return cls(
            name=str(data.get("name", "")),
            transport=str(data.get("transport", "stdio")),
            command=str(data.get("command", "")),
            args=list(data.get("args") or []),
            env=dict(data.get("env") or {}),
            url=str(data.get("url", "")),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at") or time.time()),
        )


class MCPStore:
    """Administra la base de datos de servidores MCP registrados."""

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
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    name TEXT PRIMARY KEY,
                    transport TEXT NOT NULL DEFAULT 'stdio',
                    command TEXT NOT NULL DEFAULT '',
                    args TEXT NOT NULL DEFAULT '[]',
                    env TEXT NOT NULL DEFAULT '{}',
                    url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                """
            )
            conn.commit()

    def list_all(self) -> list[MCPServerRecord]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM mcp_servers ORDER BY created_at ASC").fetchall()
            results = []
            for row in rows:
                results.append(
                    MCPServerRecord(
                        name=row["name"],
                        transport=row["transport"],
                        command=row["command"],
                        args=json.loads(row["args"]),
                        env=json.loads(row["env"]),
                        url=row["url"],
                        enabled=bool(row["enabled"]),
                        created_at=float(row["created_at"]),
                    )
                )
            return results

    def get(self, name: str) -> MCPServerRecord | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM mcp_servers WHERE name = ?", (name,)).fetchone()
            if row is None:
                return None
            return MCPServerRecord(
                name=row["name"],
                transport=row["transport"],
                command=row["command"],
                args=json.loads(row["args"]),
                env=json.loads(row["env"]),
                url=row["url"],
                enabled=bool(row["enabled"]),
                created_at=float(row["created_at"]),
            )

    def save(self, record: MCPServerRecord) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO mcp_servers (name, transport, command, args, env, url, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    transport = excluded.transport,
                    command = excluded.command,
                    args = excluded.args,
                    env = excluded.env,
                    url = excluded.url,
                    enabled = excluded.enabled,
                    created_at = excluded.created_at;
                """,
                (
                    record.name,
                    record.transport,
                    record.command,
                    json.dumps(record.args),
                    json.dumps(record.env),
                    record.url,
                    1 if record.enabled else 0,
                    record.created_at,
                ),
            )
            conn.commit()

    def delete(self, name: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
            conn.commit()
            return cursor.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE mcp_servers SET enabled = ? WHERE name = ?",
                (1 if enabled else 0, name),
            )
            conn.commit()
            return cursor.rowcount > 0

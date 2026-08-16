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
    #: Cabeceras HTTP para transport="sse". Un servidor SSE se autentica por
    #: cabecera, no por entorno: Home Assistant, por ejemplo, exige
    #: `Authorization: Bearer <token>` y sin esto no habia forma de dársela.
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """Lo mismo, pero sin los valores de `env`.

        Ahí es donde va el token de un servidor MCP —el propio ejemplo de este
        fichero es `GITHUB_PERSONAL_ACCESS_TOKEN`—, así que devolverlo tal cual
        en el listado lo pondría en pantalla y en el historial del navegador. Se
        conservan los nombres de las variables: al editar hace falta saber
        cuáles están puestas, no cuánto valen.
        """
        data = asdict(self)
        data["env"] = {key: "••••" for key in self.env}
        # `Authorization` lleva el token entero: se oculta igual que `env`.
        data["headers"] = {key: "••••" for key in self.headers}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServerRecord:
        return cls(
            name=str(data.get("name", "")),
            transport=str(data.get("transport", "stdio")),
            command=str(data.get("command", "")),
            args=list(data.get("args") or []),
            env=dict(data.get("env") or {}),
            url=str(data.get("url", "")),
            headers=dict(data.get("headers") or {}),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at") or time.time()),
        )


class MCPStore:
    """Administra la base de datos de servidores MCP registrados."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._shared: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            # Cada sqlite3.connect(":memory:") abre una base NUEVA y vacía. Si se
            # abriera una por llamada, la tabla creada aquí desaparecería y todo
            # lo escrito se perdería en la siguiente consulta. Se conserva una
            # única conexión para que el modo memoria se comporte como el de
            # fichero. `with conn:` sólo cierra la transacción, no la conexión.
            self._shared = sqlite3.connect(self.db_path, check_same_thread=False)
            self._shared.row_factory = sqlite3.Row
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
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
                    headers TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                """
            )
            # Las bases creadas antes de que existieran las cabeceras siguen ahi:
            # añadir la columna evita que actualizar el gateway rompa el registro
            # de servidores que ya funcionaban.
            columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(mcp_servers)")}
            if "headers" not in columnas:
                conn.execute(
                    "ALTER TABLE mcp_servers ADD COLUMN headers TEXT NOT NULL DEFAULT '{}'"
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
                        headers=json.loads(row["headers"] or "{}"),
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
                headers=json.loads(row["headers"] or "{}"),
                enabled=bool(row["enabled"]),
                created_at=float(row["created_at"]),
            )

    def save(self, record: MCPServerRecord) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO mcp_servers (name, transport, command, args, env, url, headers, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    transport = excluded.transport,
                    command = excluded.command,
                    args = excluded.args,
                    env = excluded.env,
                    url = excluded.url,
                    headers = excluded.headers,
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
                    json.dumps(record.headers),
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

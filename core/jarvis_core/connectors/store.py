"""Almacén SQLite cifrado para módulos de conectores administrados."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
SUPPORTED_TYPES = {"n8n", "home_assistant"}


@dataclass(frozen=True)
class ConnectorRecord:
    name: str
    connector_type: str
    config: dict[str, Any]
    enabled: bool


class ConnectorStore:
    """Guarda configuración pública y secretos cifrados con una clave maestra."""

    def __init__(self, db_path: str, master_key: str) -> None:
        if len(master_key) < 32:
            raise ValueError(
                "JARVIS_CONNECTOR_MASTER_KEY debe tener al menos 32 caracteres."
            )
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        digest = hashlib.sha256(master_key.encode("utf-8")).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(digest))
        with self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS connectors (
                    name TEXT PRIMARY KEY,
                    connector_type TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    secrets_token BLOB NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _validate(name: str, connector_type: str) -> None:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                "Nombre inválido: usa 2-32 caracteres, minúsculas, números, _ o -."
            )
        if connector_type not in SUPPORTED_TYPES:
            raise ValueError(f"Tipo de conector no soportado: {connector_type}.")

    def upsert(
        self,
        name: str,
        connector_type: str,
        config: dict[str, Any],
        secrets: dict[str, str],
        *,
        enabled: bool = True,
    ) -> None:
        self._validate(name, connector_type)
        config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        secret_json = json.dumps(secrets, ensure_ascii=False, separators=(",", ":"))
        if (
            len(config_json.encode()) > 64 * 1024
            or len(secret_json.encode()) > 64 * 1024
        ):
            raise ValueError("La configuración del conector supera 64 KiB.")
        token = self._cipher.encrypt(secret_json.encode("utf-8"))
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO connectors(name, connector_type, config_json, secrets_token, enabled)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    connector_type=excluded.connector_type,
                    config_json=excluded.config_json,
                    secrets_token=excluded.secrets_token,
                    enabled=excluded.enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (name, connector_type, config_json, token, int(enabled)),
            )

    def _decode(self, row: tuple[Any, ...]) -> ConnectorRecord:
        try:
            secrets = json.loads(self._cipher.decrypt(row[3]).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"No se pudieron descifrar las claves de {row[0]}."
            ) from exc
        config = json.loads(row[2])
        config["_secrets"] = secrets
        return ConnectorRecord(row[0], row[1], config, bool(row[4]))

    def get(self, name: str) -> ConnectorRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT name, connector_type, config_json, secrets_token, enabled FROM connectors WHERE name=?",
                (name,),
            ).fetchone()
        return self._decode(row) if row else None

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT name, connector_type, config_json, enabled, updated_at FROM connectors ORDER BY name"
            ).fetchall()
        return [
            {
                "name": row[0],
                "type": row[1],
                "enabled": bool(row[3]),
                "url": json.loads(row[2]).get("url", ""),
                "services": json.loads(row[2]).get("services", []),
                "read_actions": json.loads(row[2]).get("read_actions", []),
                "write_actions": json.loads(row[2]).get("write_actions", []),
                "updated_at": row[4],
            }
            for row in rows
        ]

    def delete(self, name: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute("DELETE FROM connectors WHERE name=?", (name,))
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

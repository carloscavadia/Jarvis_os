"""Auditoría persistente de eventos y acciones proactivas."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class ProactiveEventStore:
    """Conserva decisiones proactivas sin almacenar credenciales del conector."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS proactive_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connector TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proactive_events_created
                    ON proactive_events(id DESC);
                CREATE INDEX IF NOT EXISTS idx_proactive_events_status
                    ON proactive_events(status, updated_at);
                """
            )

    def create(
        self,
        *,
        connector: str,
        event_type: str,
        title: str,
        text: str,
        severity: str,
        policy: str,
        status: str,
        action: str = "",
        payload: dict[str, Any] | None = None,
        result: str = "",
    ) -> int:
        payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        if len(payload_json.encode("utf-8")) > 64 * 1024:
            raise ValueError("El payload del evento supera 64 KiB.")
        now = time.time()
        with self._lock, self._db:
            cursor = self._db.execute(
                """
                INSERT INTO proactive_events(
                    connector,event_type,title,text,severity,policy,status,action,
                    payload_json,result,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    connector,
                    event_type,
                    title,
                    text,
                    severity,
                    policy,
                    status,
                    action,
                    payload_json,
                    result[:12000],
                    now,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "connector": row["connector"],
            "event": row["event_type"],
            "title": row["title"],
            "text": row["text"],
            "severity": row["severity"],
            "policy": row["policy"],
            "status": row["status"],
            "action": row["action"],
            "payload": json.loads(row["payload_json"]),
            "result": row["result"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, event_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM proactive_events WHERE id=?", (event_id,)
            ).fetchone()
        return self._public(row) if row else None

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM proactive_events ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [self._public(row) for row in rows]

    def transition(
        self,
        event_id: int,
        *,
        from_status: str,
        to_status: str,
        result: str = "",
    ) -> dict[str, Any]:
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE proactive_events SET status=?, result=?, updated_at=? "
                "WHERE id=? AND status=?",
                (to_status, result[:12000], time.time(), event_id, from_status),
            )
            if not cursor.rowcount:
                raise ValueError("El evento no existe o ya fue resuelto.")
        event = self.get(event_id)
        assert event is not None
        return event

    def close(self) -> None:
        with self._lock:
            self._db.close()

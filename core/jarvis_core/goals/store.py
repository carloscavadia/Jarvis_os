"""Almacén SQLite de objetivos con pasos verificables."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

GOAL_STATUSES = {"active", "completed", "cancelled", "blocked"}
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped"}


@dataclass(frozen=True)
class GoalStep:
    id: int
    position: int
    title: str
    status: str
    verification: str
    evidence: str


@dataclass(frozen=True)
class Goal:
    id: int
    title: str
    description: str
    status: str
    created_at: float
    updated_at: float
    steps: list[GoalStep]


class GoalStore:
    def __init__(self, db_path: str) -> None:
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS goal_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    UNIQUE(goal_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status, updated_at);
                """
            )

    def create(self, title: str, description: str, steps: list[dict[str, str]]) -> int:
        now = time.time()
        with self._lock, self._conn:
            active = self._conn.execute(
                "SELECT id FROM goals WHERE status='active' LIMIT 1"
            ).fetchone()
            if active:
                raise ValueError(f"Ya existe el objetivo activo #{active['id']}.")
            cursor = self._conn.execute(
                "INSERT INTO goals(title, description, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                (title, description, now, now),
            )
            goal_id = int(cursor.lastrowid)
            self._conn.executemany(
                "INSERT INTO goal_steps(goal_id, position, title, status, verification) VALUES (?, ?, ?, 'pending', ?)",
                [
                    (goal_id, index, step["title"], step.get("verification", ""))
                    for index, step in enumerate(steps, start=1)
                ],
            )
        return goal_id

    def _goal(self, row: sqlite3.Row) -> Goal:
        step_rows = self._conn.execute(
            "SELECT * FROM goal_steps WHERE goal_id=? ORDER BY position", (row["id"],)
        ).fetchall()
        return Goal(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            steps=[
                GoalStep(
                    id=step["id"],
                    position=step["position"],
                    title=step["title"],
                    status=step["status"],
                    verification=step["verification"],
                    evidence=step["evidence"],
                )
                for step in step_rows
            ],
        )

    def get(self, goal_id: int | None = None) -> Goal | None:
        with self._lock:
            if goal_id is None:
                row = self._conn.execute(
                    "SELECT * FROM goals WHERE status='active' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM goals WHERE id=?", (goal_id,)
                ).fetchone()
            return self._goal(row) if row else None

    def update_step(
        self, goal_id: int, position: int, status: str, evidence: str
    ) -> Goal:
        if status not in STEP_STATUSES:
            raise ValueError("Estado de paso inválido.")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE goal_steps SET status=?, evidence=? WHERE goal_id=? AND position=?",
                (status, evidence, goal_id, position),
            )
            if not cursor.rowcount:
                raise ValueError("Objetivo o paso inexistente.")
            self._conn.execute(
                "UPDATE goals SET updated_at=? WHERE id=?", (time.time(), goal_id)
            )
        goal = self.get(goal_id)
        assert goal is not None
        return goal

    def set_status(self, goal_id: int, status: str) -> Goal:
        if status not in GOAL_STATUSES - {"active"}:
            raise ValueError("Estado final de objetivo inválido.")
        with self._lock, self._conn:
            if status == "completed":
                pending = self._conn.execute(
                    "SELECT COUNT(*) FROM goal_steps WHERE goal_id=? AND status!='completed'",
                    (goal_id,),
                ).fetchone()[0]
                if pending:
                    raise ValueError(
                        "No se puede completar: quedan pasos sin verificar."
                    )
            cursor = self._conn.execute(
                "UPDATE goals SET status=?, updated_at=? WHERE id=? AND status='active'",
                (status, time.time(), goal_id),
            )
            if not cursor.rowcount:
                raise ValueError("El objetivo no existe o ya está cerrado.")
        goal = self.get(goal_id)
        assert goal is not None
        return goal

    @staticmethod
    def serialize(goal: Goal) -> str:
        completed = sum(step.status == "completed" for step in goal.steps)
        return json.dumps(
            {
                "goal_id": goal.id,
                "title": goal.title,
                "description": goal.description,
                "status": goal.status,
                "progress": {"completed": completed, "total": len(goal.steps)},
                "steps": [
                    {
                        "position": step.position,
                        "title": step.title,
                        "status": step.status,
                        "verification": step.verification,
                        "evidence": step.evidence,
                    }
                    for step in goal.steps
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

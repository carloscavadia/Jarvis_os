"""Staging persistente, aplicación atómica y rollback de autorreparaciones."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import tomllib


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RepairStore:
    """Gestiona propuestas sin permitir rutas fuera de una raíz explícita."""

    def __init__(self, db_path: str, root: str, max_file_bytes: int = 512 * 1024) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS repair_proposals (
                    id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    original BLOB NOT NULL,
                    original_hash TEXT NOT NULL,
                    proposed BLOB NOT NULL,
                    proposed_hash TEXT NOT NULL,
                    validator TEXT NOT NULL,
                    validation_result TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_at TEXT
                )"""
            )

    def _resolve(self, raw_path: str) -> Path:
        relative = Path(raw_path)
        if relative.is_absolute() or not raw_path or "\0" in raw_path:
            raise ValueError("La ruta de reparación debe ser relativa.")
        lowered_parts = {part.lower() for part in relative.parts}
        filename = relative.name.lower()
        if (
            lowered_parts.intersection({".git", ".ssh"})
            or filename == ".env"
            or filename.startswith(".env.")
            or relative.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
        ):
            raise ValueError("No se permite autorreparar credenciales ni llaves.")
        cursor = self.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValueError("No se permiten enlaces simbólicos en reparaciones.")
        candidate = (self.root / relative).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("La ruta sale de la raíz de reparación.") from exc
        if candidate == self.root or candidate.is_symlink():
            raise ValueError("Objetivo de reparación no permitido.")
        return candidate

    def inspect(self, raw_path: str) -> dict[str, Any]:
        path = self._resolve(raw_path)
        if not path.is_file():
            raise ValueError("El archivo objetivo no existe.")
        content = path.read_bytes()
        if len(content) > self.max_file_bytes:
            raise ValueError("El archivo supera el límite de autorreparación.")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Solo se admiten archivos UTF-8.") from exc
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": _digest(content),
            "bytes": len(content),
            "content": text,
        }

    @staticmethod
    def _validate(content: str, validator: str) -> str:
        if not content.strip():
            raise ValueError("La propuesta no puede quedar vacía.")
        if validator == "python":
            ast.parse(content)
            return "Sintaxis Python válida."
        if validator == "json":
            json.loads(content)
            return "JSON válido."
        if validator == "toml":
            tomllib.loads(content)
            return "TOML válido."
        if validator == "text":
            return "Texto UTF-8 no vacío."
        raise ValueError("Validador no permitido.")

    def propose(
        self, raw_path: str, content: str, rationale: str, validator: str
    ) -> dict[str, Any]:
        if not rationale.strip() or len(rationale) > 2000:
            raise ValueError("El diagnóstico debe tener entre 1 y 2000 caracteres.")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise ValueError("La propuesta supera el límite permitido.")
        inspected = self.inspect(raw_path)
        validation = self._validate(content, validator)
        proposal_id = uuid.uuid4().hex[:16]
        original = inspected["content"].encode("utf-8")
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO repair_proposals(
                    id,target,rationale,original,original_hash,proposed,proposed_hash,
                    validator,validation_result,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposal_id,
                    inspected["path"],
                    rationale.strip(),
                    original,
                    inspected["sha256"],
                    encoded,
                    _digest(encoded),
                    validator,
                    validation,
                    "validated",
                ),
            )
        return self.get(proposal_id)

    def get(self, proposal_id: str, *, include_content: bool = False) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM repair_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Propuesta de reparación no encontrada.")
        result = {
            "id": row["id"],
            "target": row["target"],
            "rationale": row["rationale"],
            "original_hash": row["original_hash"],
            "proposed_hash": row["proposed_hash"],
            "validator": row["validator"],
            "validation": row["validation_result"],
            "status": row["status"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
        }
        if include_content:
            result["proposed_content"] = bytes(row["proposed"]).decode("utf-8")
        return result

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM repair_proposals ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o7777 if path.exists() else 0o600
        descriptor, temporary = tempfile.mkstemp(prefix=".jarvis-repair-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def apply(self, proposal_id: str) -> dict[str, Any]:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM repair_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if row is None or row["status"] != "validated":
                raise ValueError("La propuesta no está disponible para aplicar.")
            path = self._resolve(row["target"])
            current = path.read_bytes() if path.is_file() else b""
            if _digest(current) != row["original_hash"]:
                raise ValueError(
                    "El archivo cambió desde el diagnóstico; crea una propuesta nueva."
                )
            proposed = bytes(row["proposed"])
            self._atomic_write(path, proposed)
            self._db.execute(
                "UPDATE repair_proposals SET status='applied', applied_at=CURRENT_TIMESTAMP WHERE id=?",
                (proposal_id,),
            )
        return self.get(proposal_id)

    def rollback(self, proposal_id: str) -> dict[str, Any]:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM repair_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if row is None or row["status"] != "applied":
                raise ValueError("La reparación no está aplicada.")
            path = self._resolve(row["target"])
            current = path.read_bytes() if path.is_file() else b""
            if _digest(current) != row["proposed_hash"]:
                raise ValueError("El archivo cambió después de aplicar; rollback bloqueado.")
            self._atomic_write(path, bytes(row["original"]))
            self._db.execute(
                "UPDATE repair_proposals SET status='rolled_back' WHERE id=?",
                (proposal_id,),
            )
        return self.get(proposal_id)

    def close(self) -> None:
        with self._lock:
            self._db.close()

"""SQLite による操作履歴・ファイル処理状態・分類体系変更提案の管理。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from taxonomy.model import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_path TEXT NOT NULL,
    dest_path TEXT,
    created_at TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    sha256 TEXT,
    category_id TEXT,
    category_name TEXT,
    reason TEXT,
    result TEXT NOT NULL,
    error TEXT,
    undone_at TEXT,
    undo_error TEXT
);
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    status TEXT NOT NULL,
    category_id TEXT,
    confidence REAL,
    reason TEXT,
    detail TEXT,
    origin TEXT,
    attempts INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    reason TEXT,
    operations TEXT NOT NULL,
    related_files TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_ops_created ON operations(created_at);
"""

# ファイル状態
F_PENDING = "pending"        # 検出済み・未処理
F_PROCESSING = "processing"
F_READY = "ready"            # 分類済み・プレビュー/手動実行待ち（安全条件は満たす）
F_REVIEW = "review"          # 要確認（自動移動しない）
F_MOVED = "moved"
F_SKIPPED = "skipped"        # 対象外
F_FAILED = "failed"


def path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass
class Operation:
    id: int
    src_path: str
    dest_path: str | None
    created_at: str
    size: int | None
    mtime: float | None
    sha256: str | None
    category_id: str | None
    category_name: str | None
    reason: str | None
    result: str
    error: str | None
    undone_at: str | None
    undo_error: str | None


@dataclass
class FileRecord:
    key: str
    path: str
    size: int | None
    mtime: float | None
    status: str
    category_id: str | None
    confidence: float | None
    reason: str | None
    detail: dict
    origin: str | None
    attempts: int
    updated_at: str


@dataclass
class Proposal:
    id: str
    kind: str
    title: str
    reason: str
    operations: list[dict]
    related_files: list[str]
    status: str
    created_at: str
    decided_at: str | None
    source: str | None


class HistoryDB:
    def __init__(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, args))

    # ---- app state ----
    def get_state(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM app_state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_state(self, key: str, value: str) -> None:
        self._exec("INSERT INTO app_state (key, value) VALUES (?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---- operations ----
    def add_operation(self, *, src_path: str, dest_path: str | None, size: int | None, mtime: float | None,
                      sha256: str | None, category_id: str | None, category_name: str | None,
                      reason: str | None, result: str, error: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO operations (src_path, dest_path, created_at, size, mtime, sha256, category_id,"
            " category_name, reason, result, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (src_path, dest_path, now_iso(), size, mtime, sha256, category_id, category_name, reason, result, error),
        )
        return int(cur.lastrowid)

    def get_operation(self, op_id: int) -> Operation | None:
        rows = self._query("SELECT * FROM operations WHERE id=?", (op_id,))
        return Operation(**dict(rows[0])) if rows else None

    def list_operations(self, limit: int = 500) -> list[Operation]:
        rows = self._query("SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,))
        return [Operation(**dict(r)) for r in rows]

    def mark_undone(self, op_id: int) -> None:
        self._exec("UPDATE operations SET result='undone', undone_at=?, undo_error=NULL WHERE id=?",
                   (now_iso(), op_id))

    def mark_undo_failed(self, op_id: int, error: str) -> None:
        self._exec("UPDATE operations SET undo_error=? WHERE id=?", (error, op_id))

    def counts(self) -> dict[str, int]:
        rows = self._query("SELECT result, COUNT(*) AS n FROM operations GROUP BY result")
        return {r["result"]: r["n"] for r in rows}

    # ---- files ----
    def upsert_file(self, path: str, *, status: str, size: int | None = None, mtime: float | None = None,
                    category_id: str | None = None, confidence: float | None = None, reason: str | None = None,
                    detail: dict | None = None, origin: str | None = None, attempts: int | None = None) -> None:
        key = path_key(path)
        existing = self.get_file(path)
        if existing and origin is None:
            origin = existing.origin
        if existing and attempts is None:
            attempts = existing.attempts
        self._exec(
            "INSERT INTO files (key, path, size, mtime, status, category_id, confidence, reason, detail, origin,"
            " attempts, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET path=excluded.path, size=excluded.size, mtime=excluded.mtime,"
            " status=excluded.status, category_id=excluded.category_id, confidence=excluded.confidence,"
            " reason=excluded.reason, detail=excluded.detail, origin=excluded.origin, attempts=excluded.attempts,"
            " updated_at=excluded.updated_at",
            (key, str(path), size, mtime, status, category_id, confidence, reason,
             json.dumps(detail or {}, ensure_ascii=False), origin, attempts or 0, now_iso()),
        )

    def get_file(self, path: str | Path) -> FileRecord | None:
        rows = self._query("SELECT * FROM files WHERE key=?", (path_key(path),))
        return self._file_row(rows[0]) if rows else None

    def list_files(self, statuses: tuple[str, ...] | None = None) -> list[FileRecord]:
        if statuses:
            q = ",".join("?" * len(statuses))
            rows = self._query(f"SELECT * FROM files WHERE status IN ({q}) ORDER BY updated_at DESC", statuses)
        else:
            rows = self._query("SELECT * FROM files ORDER BY updated_at DESC")
        return [self._file_row(r) for r in rows]

    def delete_file(self, path: str | Path) -> None:
        self._exec("DELETE FROM files WHERE key=?", (path_key(path),))

    def file_counts(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM files GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _file_row(r: sqlite3.Row) -> FileRecord:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except ValueError:
            d["detail"] = {}
        return FileRecord(**d)

    # ---- proposals ----
    def add_proposal(self, *, kind: str, title: str, reason: str, operations: list[dict],
                     related_files: list[str] | None = None, source: str = "ai") -> str:
        pid = "prop-" + uuid.uuid4().hex[:12]
        self._exec(
            "INSERT INTO proposals (id, kind, title, reason, operations, related_files, status, created_at, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, kind, title, reason, json.dumps(operations, ensure_ascii=False),
             json.dumps(related_files or [], ensure_ascii=False), "pending", now_iso(), source),
        )
        return pid

    def update_proposal(self, pid: str, *, operations: list[dict] | None = None,
                        related_files: list[str] | None = None) -> None:
        p = self.get_proposal(pid)
        if not p:
            return
        self._exec("UPDATE proposals SET operations=?, related_files=? WHERE id=?",
                   (json.dumps(operations if operations is not None else p.operations, ensure_ascii=False),
                    json.dumps(related_files if related_files is not None else p.related_files, ensure_ascii=False),
                    pid))

    def set_proposal_status(self, pid: str, status: str) -> None:
        self._exec("UPDATE proposals SET status=?, decided_at=? WHERE id=?", (status, now_iso(), pid))

    def get_proposal(self, pid: str) -> Proposal | None:
        rows = self._query("SELECT * FROM proposals WHERE id=?", (pid,))
        return self._proposal_row(rows[0]) if rows else None

    def list_proposals(self, status: str | None = "pending") -> list[Proposal]:
        if status:
            rows = self._query("SELECT * FROM proposals WHERE status=? ORDER BY created_at", (status,))
        else:
            rows = self._query("SELECT * FROM proposals ORDER BY created_at")
        return [self._proposal_row(r) for r in rows]

    @staticmethod
    def _proposal_row(r: sqlite3.Row) -> Proposal:
        d: dict[str, Any] = dict(r)
        d["operations"] = json.loads(d["operations"] or "[]")
        d["related_files"] = json.loads(d["related_files"] or "[]")
        d["reason"] = d.get("reason") or ""
        return Proposal(**d)

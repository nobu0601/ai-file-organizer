"""埋め込みベクトルの保存（SQLite）。

- embeddings: ファイルごとの埋め込みのキャッシュ（パス・サイズ・更新日時・モデルが同じなら再計算しない）
- category_members: カテゴリの特徴としてのメンバーの埋め込み（承認・移動のたびに増える = 学習）

正式な分類体系（folder_map.json）とは別に持つ。移動のたびに分類体系のバージョンが上がるのを避けるため。
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path

from taxonomy.model import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    key TEXT NOT NULL,
    model TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    vec TEXT NOT NULL,
    PRIMARY KEY (key, model)
);
CREATE TABLE IF NOT EXISTS category_members (
    category_id TEXT NOT NULL,
    member TEXT NOT NULL,
    model TEXT NOT NULL,
    vec TEXT NOT NULL,
    source TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (category_id, member, model)
);
CREATE INDEX IF NOT EXISTS idx_members_cat ON category_members(category_id, model);
"""

MAX_MEMBERS = 200   # カテゴリごとに覚える件数の上限（古いものから捨てる）


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def mean(vs: list[list[float]]) -> list[float]:
    if not vs:
        return []
    return normalize([sum(c) / len(vs) for c in zip(*vs)])


def _dump(v: list[float]) -> str:
    return json.dumps([round(x, 5) for x in v])


class VectorStore:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._centroids: dict[tuple[str, str], list[float]] = {}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- ファイルの埋め込みキャッシュ ----
    def get_cached(self, key: str, model: str, size: int, mtime: float) -> list[float] | None:
        with self._lock:
            row = self._conn.execute("SELECT size, mtime, vec FROM embeddings WHERE key=? AND model=?",
                                     (key, model)).fetchone()
        if row and row[0] == size and row[1] is not None and abs(row[1] - mtime) < 1e-6:
            return json.loads(row[2])
        return None

    def put_cached(self, key: str, model: str, size: int, mtime: float, vec: list[float]) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO embeddings (key, model, size, mtime, vec) VALUES (?,?,?,?,?)",
                               (key, model, size, mtime, _dump(vec)))
            self._conn.commit()

    # ---- カテゴリの特徴 ----
    def add_member(self, category_id: str, member: str, model: str, vec: list[float], source: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO category_members (category_id, member, model, vec, source, added_at)"
                " VALUES (?,?,?,?,?,?)", (category_id, member, model, _dump(vec), source, now_iso()))
            # 上限を超えた古いものを捨てる
            self._conn.execute(
                "DELETE FROM category_members WHERE category_id=? AND model=? AND member NOT IN ("
                " SELECT member FROM category_members WHERE category_id=? AND model=?"
                " ORDER BY added_at DESC LIMIT ?)", (category_id, model, category_id, model, MAX_MEMBERS))
            self._conn.commit()
            self._centroids.pop((category_id, model), None)

    def remove_member(self, member: str) -> None:
        with self._lock:
            cats = [r[0] for r in self._conn.execute("SELECT DISTINCT category_id FROM category_members WHERE member=?",
                                                    (member,))]
            self._conn.execute("DELETE FROM category_members WHERE member=?", (member,))
            self._conn.commit()
            for c in cats:
                for k in [k for k in self._centroids if k[0] == c]:
                    self._centroids.pop(k, None)

    def reassign(self, source_id: str, target_id: str) -> None:
        """カテゴリの統合: 統合元の特徴を統合先へ移す。"""
        with self._lock:
            self._conn.execute("UPDATE OR REPLACE category_members SET category_id=? WHERE category_id=?",
                               (target_id, source_id))
            self._conn.commit()
            self._centroids = {k: v for k, v in self._centroids.items() if k[0] not in (source_id, target_id)}

    def count(self, category_id: str, model: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM category_members WHERE category_id=? AND model=?",
                                      (category_id, model)).fetchone()[0]

    def centroid(self, category_id: str, model: str) -> list[float]:
        key = (category_id, model)
        with self._lock:
            if key not in self._centroids:
                rows = self._conn.execute("SELECT vec FROM category_members WHERE category_id=? AND model=?",
                                          key).fetchall()
                self._centroids[key] = mean([json.loads(r[0]) for r in rows])
            return self._centroids[key]

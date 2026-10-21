"""ファイルイベントの処理キュー。

- 同じパスへの連続イベントを 1 件に集約する
- サイズと更新日時が連続して N 回同じで、最後の変更から quiet 秒以上経ったら「安定」とみなす
- 使用中（開けない）なら再試行し、上限を超えたら失敗にする
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from history.db import path_key

Q_WAITING = "待機中"
Q_PROCESSING = "処理中"
Q_DONE = "完了"
Q_REVIEW = "要確認"
Q_READY = "実行待ち"
Q_SKIPPED = "対象外"
Q_FAILED = "失敗"
FINISHED = {Q_DONE, Q_REVIEW, Q_READY, Q_SKIPPED, Q_FAILED}

ORIGIN_PRIORITY = {"scan": 0, "startup": 1, "event": 2}


@dataclass
class QueueItem:
    path: str
    origin: str
    watch_root: str | None
    status: str = Q_WAITING
    first_seen: float = field(default_factory=time.time)
    last_event: float = field(default_factory=time.time)
    snapshots: list[tuple[int, float]] = field(default_factory=list)
    attempts: int = 0
    message: str = ""
    dirty: bool = False
    finished_at: float = 0.0


def default_open_check(path: str) -> bool:
    """他のプロセスが書き込み中でないか（Windows では排他ロックで開けない）。"""
    try:
        with open(path, "rb"):
            pass
        if os.name == "nt" and os.access(path, os.W_OK):
            with open(path, "r+b"):
                pass
        return True
    except OSError:
        return False


class ProcessingQueue:
    def __init__(self, checks: int = 3, quiet_seconds: float = 2.0, max_attempts: int = 20,
                 open_check=default_open_check, keep_finished: int = 300) -> None:
        self.checks = checks
        self.quiet_seconds = quiet_seconds
        self.max_attempts = max_attempts
        self.open_check = open_check
        self.keep_finished = keep_finished
        self._items: dict[str, QueueItem] = {}
        self._lock = threading.RLock()

    def enqueue(self, path: str | Path, origin: str = "event", watch_root: str | None = None,
                now: float | None = None) -> bool:
        """追加したら True。既存の待機中項目に集約した場合は False。"""
        now = time.time() if now is None else now
        key = path_key(path)
        with self._lock:
            item = self._items.get(key)
            if item is not None and item.status == Q_PROCESSING:
                item.dirty = True
                item.last_event = now
                return False
            if item is not None and item.status == Q_WAITING:
                item.last_event = now
                item.snapshots.clear()
                if ORIGIN_PRIORITY.get(origin, 0) > ORIGIN_PRIORITY.get(item.origin, 0):
                    item.origin = origin
                return False
            self._items[key] = QueueItem(path=str(path), origin=origin, watch_root=watch_root,
                                         first_seen=now, last_event=now)
            self._trim()
            return True

    def poll_stability(self, now: float | None = None) -> list[QueueItem]:
        """待機中の項目のスナップショットを 1 回取り、処理可能になった項目を返す（処理中に遷移）。"""
        now = time.time() if now is None else now
        ready: list[QueueItem] = []
        with self._lock:
            waiting = [i for i in self._items.values() if i.status == Q_WAITING]
        for item in waiting:
            try:
                st = os.stat(item.path)
                snap = (st.st_size, st.st_mtime)
            except FileNotFoundError:
                self._finish(item, Q_SKIPPED, "ファイルが無くなりました")
                continue
            except OSError as e:
                self._retry(item, f"属性を取得できません: {e}")
                continue
            with self._lock:
                if item.snapshots and item.snapshots[-1] != snap:
                    item.snapshots.clear()
                    item.last_event = now
                item.snapshots.append(snap)
                item.snapshots = item.snapshots[-self.checks:]
                stable = len(item.snapshots) >= self.checks and len(set(item.snapshots)) == 1
                last_change = max(item.last_event, snap[1])
                quiet = (now - last_change) >= self.quiet_seconds
                if not stable or not quiet:
                    item.message = f"書き込み完了を確認中 ({len(item.snapshots)}/{self.checks})"
                    continue
            if not self.open_check(item.path):
                self._retry(item, "ファイルが使用中です。再試行します")
                continue
            with self._lock:
                item.status = Q_PROCESSING
                item.message = "処理中"
                item.dirty = False
            ready.append(item)
        return ready

    def _retry(self, item: QueueItem, message: str) -> None:
        with self._lock:
            item.attempts += 1
            item.snapshots.clear()
            item.message = message
            if item.attempts >= self.max_attempts:
                self._finish(item, Q_FAILED, f"{message}（再試行上限）")

    def _finish(self, item: QueueItem, status: str, message: str) -> None:
        with self._lock:
            item.status = status
            item.message = message
            item.finished_at = time.time()

    def finish(self, path: str, status: str, message: str) -> None:
        with self._lock:
            item = self._items.get(path_key(path))
            if item is None:
                return
            if item.dirty and status != Q_FAILED and os.path.exists(item.path):
                # 処理中に再度更新された: もう一度安定確認からやり直す
                item.status = Q_WAITING
                item.snapshots.clear()
                item.dirty = False
                item.message = "処理中に更新されたため再確認します"
                return
            self._finish(item, status, message)

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for i in self._items.values() if i.status in (Q_WAITING, Q_PROCESSING))

    def snapshot(self) -> list[QueueItem]:
        with self._lock:
            return sorted((QueueItem(**vars(i)) for i in self._items.values()),
                          key=lambda i: (i.status in FINISHED, -i.last_event))

    def clear_finished(self) -> None:
        with self._lock:
            for k in [k for k, i in self._items.items() if i.status in FINISHED]:
                del self._items[k]

    def _trim(self) -> None:
        done = sorted((i for i in self._items.values() if i.status in FINISHED), key=lambda i: i.finished_at)
        for i in done[: max(0, len(done) - self.keep_finished)]:
            self._items.pop(path_key(i.path), None)

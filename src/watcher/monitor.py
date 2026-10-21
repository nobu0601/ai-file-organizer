"""watchdog によるフォルダ監視。イベントはキューへ渡すだけで、ここでは処理しない。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, on_file: Callable[[str, str], None]) -> None:
        self.root = root
        self.on_file = on_file

    def _emit(self, path: str) -> None:
        try:
            self.on_file(path, str(self.root))
        except Exception:  # 監視スレッドを落とさない
            log.exception("イベント処理でエラー: %s", path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # ブラウザの「.crdownload → 本来の名前」へのリネームもここで拾う
        if not event.is_directory:
            self._emit(event.dest_path)


class FolderMonitor:
    def __init__(self, on_file: Callable[[str, str], None]) -> None:
        self.on_file = on_file
        self._observer: Observer | None = None

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def start(self, folders: list[tuple[str, bool]]) -> list[str]:
        """(パス, 再帰) のリストを監視開始。開始できなかったフォルダのエラーを返す。"""
        self.stop()
        errors = []
        obs = Observer()
        for path, recursive in folders:
            p = Path(path)
            if not p.is_dir():
                errors.append(f"フォルダが見つかりません: {path}")
                continue
            try:
                obs.schedule(_Handler(p, self.on_file), str(p), recursive=recursive)
            except OSError as e:
                errors.append(f"監視できません: {path}: {e}")
        obs.daemon = True
        obs.start()
        self._observer = obs
        return errors

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

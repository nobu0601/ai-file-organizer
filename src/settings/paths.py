"""アプリ専用データ領域のパス解決。

ログ・SQLite・分類体系データは整理対象フォルダとは別の場所に置く。
Windows では %LOCALAPPDATA%\\AIFileOrganizer\\ を使う。
テストや開発時は環境変数 AIFO_DATA_DIR で上書きできる。
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "AIFileOrganizer"


def app_data_dir() -> Path:
    override = os.environ.get("AIFO_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / APP_NAME
    return base


class AppPaths:
    """データ領域内の各ファイル・フォルダの場所をまとめる。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else app_data_dir()

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def database(self) -> Path:
        return self.root / "history.sqlite3"

    @property
    def vectors(self) -> Path:
        return self.root / "vectors.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def taxonomy_dir(self) -> Path:
        return self.root / "taxonomy"

    @property
    def folder_map_json(self) -> Path:
        return self.taxonomy_dir / "folder_map.json"

    @property
    def folder_map_md(self) -> Path:
        return self.taxonomy_dir / "folder_map.md"

    @property
    def history_dir(self) -> Path:
        return self.taxonomy_dir / "folder_map_history"

    def ensure(self) -> "AppPaths":
        for p in (self.root, self.log_dir, self.taxonomy_dir, self.history_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self


def default_organize_root() -> Path:
    """整理先の既定値（ユーザーのドキュメント配下）。"""
    return Path.home() / "Documents" / "AI整理フォルダ"


def default_downloads() -> Path:
    return Path.home() / "Downloads"

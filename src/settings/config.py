"""JSON 設定の読み書き。

既定値は安全側（プレビューモード ON、自動移動 OFF、自動起動 OFF）。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from settings.paths import default_organize_root

DEFAULT_EXCLUDED_DIRS = [
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", ".vs", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
    "$RECYCLE.BIN", "System Volume Information",
]

DEFAULT_EXCLUDED_EXTENSIONS = [
    # 実行・システム系
    ".exe", ".dll", ".sys", ".msi", ".bat", ".cmd", ".ps1", ".lnk", ".url",
    # 秘密鍵・認証情報
    ".pem", ".key", ".p12", ".pfx", ".kdbx", ".gpg", ".asc", ".ppk",
    # ロック・一時
    ".lock", ".lck", ".tmp", ".part", ".crdownload", ".download", ".partial", ".swp",
    # 仮想マシン・ディスクイメージ
    ".vmdk", ".vdi", ".vhd", ".vhdx", ".qcow2", ".ova", ".ovf",
    # データベース
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".ldf", ".mdf",
]

DEFAULT_EXCLUDED_NAMES = [
    ".env", ".env.*", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.secret",
    "credentials*", "*credential*", "*password*", "*passwd*", "secrets.*", ".npmrc",
    ".pypirc", ".netrc", "*.lock", "package-lock.json", "yarn.lock", "poetry.lock",
    "Pipfile.lock", "desktop.ini", "thumbs.db", "~$*", ".~lock.*", ".DS_Store",
]

DEFAULT_SENSITIVE_KEYWORDS = [
    "password", "passwd", "パスワード", "暗証番号", "secret_key", "api_key", "apikey",
    "private key", "BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY", "access_token",
    "マイナンバー", "個人番号", "クレジットカード", "口座番号", "社外秘", "confidential",
]


@dataclass
class OllamaSettings:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    # "auto": インストール済みモデルから PC のメモリに合う最良のものを自動で使う
    model: str = "auto"
    # 意味の近さで分類する埋め込みモデル。インストールされていれば自動で使う
    embed_model: str = "bge-m3"
    timeout_seconds: float = 60.0


@dataclass
class WatchFolder:
    path: str
    recursive: bool = False


@dataclass
class Settings:
    watch_folders: list[WatchFolder] = field(default_factory=list)
    organize_root: str = field(default_factory=lambda: str(default_organize_root()))
    # True: 整理するフォルダの中に直接フォルダを作る（例: ダウンロード\請求書）。False: organize_root の下に作る
    organize_in_place: bool = True

    preview_mode: bool = True
    auto_classify: bool = True
    auto_move: bool = False
    confidence_threshold: float = 0.85

    stability_checks: int = 3
    stability_quiet_seconds: float = 2.0
    stability_poll_seconds: float = 1.0
    max_retries: int = 20

    max_parse_bytes: int = 4 * 1024 * 1024
    max_text_chars: int = 20000
    use_sha256: bool = True

    excluded_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_DIRS))
    excluded_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_EXTENSIONS))
    excluded_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_NAMES))
    excluded_paths: list[str] = field(default_factory=list)
    sensitive_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_SENSITIVE_KEYWORDS))

    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    autostart: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        s = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "watch_folders":
                s.watch_folders = [
                    WatchFolder(path=str(w.get("path", "")), recursive=bool(w.get("recursive", False)))
                    for w in value if isinstance(w, dict) and w.get("path")
                ]
            elif key == "ollama" and isinstance(value, dict):
                o = OllamaSettings()
                for ok in ("enabled", "base_url", "model", "embed_model", "timeout_seconds"):
                    if ok in value:
                        setattr(o, ok, value[ok])
                s.ollama = o
            else:
                setattr(s, key, value)
        s.confidence_threshold = min(1.0, max(0.0, float(s.confidence_threshold)))
        s.stability_checks = max(1, int(s.stability_checks))
        return s


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        with open(path, encoding="utf-8") as f:
            return Settings.from_dict(json.load(f))
    except (OSError, ValueError):
        # 壊れた設定は安全な既定値で起動する（元ファイルは残す）
        return Settings()


def save_settings(settings: Settings, path: Path) -> None:
    atomic_write_text(path, json.dumps(settings.to_dict(), ensure_ascii=False, indent=2))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

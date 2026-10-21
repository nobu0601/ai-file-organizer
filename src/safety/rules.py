"""誤移動を防ぐための安全チェック。

ここでの判定は分類結果（AI 出力）とは独立しており、
どのような分類結果でもここを通らないファイル操作は行わない。
"""
from __future__ import annotations

import fnmatch
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from settings.config import Settings

FILE_ATTRIBUTE_REPARSE_POINT = 0x400

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 開発環境のルートを示すマーカー。これがある階層の配下にあるファイルは要確認にする。
PROJECT_MARKERS = (
    ".git", ".hg", ".svn", "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "CMakeLists.txt", "Makefile", ".sln", ".csproj", ".vcxproj",
)

# ダウンロード・コピー中を示す一時ファイル
IN_PROGRESS_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")


@dataclass
class SafetyVerdict:
    allowed: bool
    reason: str = ""


def is_link_or_junction(path: Path) -> bool:
    """シンボリックリンク・ジャンクション・その他の再解析ポイントを検出する。"""
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction and isjunction(path):
            return True
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            return True
        return stat.S_ISLNK(st.st_mode)
    except OSError:
        return False


def path_has_link(path: Path, stop_at: Path | None = None) -> bool:
    """path 自身およびその親（stop_at まで）にリンクが含まれるか。"""
    p = Path(os.path.abspath(path))
    stop = Path(os.path.abspath(stop_at)) if stop_at else None
    while True:
        if p.exists() or p.is_symlink():
            if is_link_or_junction(p):
                return True
        if stop is not None and _same(p, stop):
            return False
        parent = p.parent
        if parent == p:
            return False
        p = parent


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _same(a: Path | str, b: Path | str) -> bool:
    return _norm(a) == _norm(b)


def is_within(path: Path | str, root: Path | str) -> bool:
    p, r = _norm(path), _norm(root)
    if p == r:
        return True
    return p.startswith(r.rstrip(os.sep) + os.sep)


def safe_component(name: str, max_len: int = 80) -> str:
    """フォルダ名・ファイル名の 1 要素として安全な文字列にする（Windows 基準）。"""
    s = _INVALID_CHARS.sub("_", str(name)).strip()
    s = s.replace("..", "_")
    s = s.rstrip(" .")
    if not s:
        s = "_"
    if s.split(".")[0].upper() in WINDOWS_RESERVED:
        s = "_" + s
    return s[:max_len].rstrip(" .") or "_"


def safe_relative_path(rel: str) -> str:
    """AI などから来た相対パスを安全化する。絶対パスや .. は認めない。"""
    parts = [p for p in re.split(r"[\\/]+", str(rel)) if p not in ("", ".")]
    cleaned = [safe_component(p) for p in parts if p != ".."]
    return "/".join(cleaned)


class SafetyChecker:
    def __init__(self, settings: Settings, protected_roots: Iterable[Path] = ()) -> None:
        self.settings = settings
        self.protected_roots = [Path(p) for p in protected_roots]
        self._dirs = {d.lower() for d in settings.excluded_dirs}
        self._exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in settings.excluded_extensions}
        self._names = [n.lower() for n in settings.excluded_names]
        self._paths = [Path(p) for p in settings.excluded_paths]

    # ---- 入力ファイル側 ----
    def check_source(self, path: Path, watch_root: Path | None = None) -> SafetyVerdict:
        """移動元として扱ってよいファイルか（除外対象なら allowed=False）。"""
        path = Path(path)
        name = path.name
        lname = name.lower()
        if is_link_or_junction(path):
            return SafetyVerdict(False, "シンボリックリンク/ジャンクションは追跡しません")
        if watch_root is not None and path_has_link(path.parent, stop_at=watch_root):
            return SafetyVerdict(False, "リンク経由のパスは追跡しません")
        for root in self.protected_roots:
            if is_within(path, root):
                return SafetyVerdict(False, f"アプリ管理領域または整理先のため対象外: {root}")
        for p in self._paths:
            if is_within(path, p):
                return SafetyVerdict(False, f"除外フォルダに含まれます: {p}")
        rel_parts = path.parts
        if watch_root is not None and is_within(path, watch_root):
            try:
                rel_parts = path.relative_to(watch_root).parts
            except ValueError:
                rel_parts = path.parts
        for part in rel_parts[:-1]:
            if part.lower() in self._dirs:
                return SafetyVerdict(False, f"除外フォルダ配下です: {part}")
        if lname.endswith(IN_PROGRESS_SUFFIXES):
            return SafetyVerdict(False, "ダウンロード/コピー途中の一時ファイルです")
        ext = path.suffix.lower()
        if ext in self._exts:
            return SafetyVerdict(False, f"移動禁止の拡張子です: {ext}")
        for pat in self._names:
            if fnmatch.fnmatch(lname, pat):
                return SafetyVerdict(False, f"移動禁止のファイル名パターンです: {pat}")
        return SafetyVerdict(True)

    def is_dir_excluded(self, dir_path: Path) -> bool:
        if dir_path.name.lower() in self._dirs:
            return True
        if is_link_or_junction(dir_path):
            return True
        for root in self.protected_roots:
            if is_within(dir_path, root):
                return True
        return any(is_within(dir_path, p) for p in self._paths)

    def find_project_root(self, path: Path, watch_root: Path | None) -> Path | None:
        """ファイルが開発プロジェクト（Git リポジトリ等）の内部にあればそのルートを返す。"""
        p = Path(path).parent
        stop = Path(watch_root) if watch_root else None
        while True:
            try:
                entries = {e.lower() for e in os.listdir(p)}
            except OSError:
                entries = set()
            for marker in PROJECT_MARKERS:
                if marker.lower() in entries:
                    return p
                if marker in (".sln", ".csproj", ".vcxproj") and any(e.endswith(marker) for e in entries):
                    return p
            if stop is not None and _same(p, stop):
                return None
            if p.parent == p:
                return None
            p = p.parent

    def sensitive_reason(self, path: Path, text: str) -> str:
        lname = path.name.lower()
        for kw in ("secret", "credential", "password", "passwd", "token", "private", "機密", "社外秘", "パスワード"):
            if kw in lname:
                return f"ファイル名に機密を示す語があります: {kw}"
        lowered = text[:200000].lower() if text else ""
        for kw in self.settings.sensitive_keywords:
            if kw.lower() in lowered:
                return f"本文に機密情報の可能性がある語があります: {kw}"
        return ""

    # ---- 出力先側 ----
    def check_destination(self, dest: Path, organize_root: Path) -> SafetyVerdict:
        dest = Path(dest)
        if not is_within(dest, organize_root) or _same(dest, organize_root):
            return SafetyVerdict(False, "移動先が整理先フォルダの外です")
        try:
            rel = Path(os.path.abspath(dest)).relative_to(Path(os.path.abspath(organize_root)))
        except ValueError:
            return SafetyVerdict(False, "移動先パスを検証できません")
        for part in rel.parts:
            if part in ("..", ".") or safe_component(part) != part:
                return SafetyVerdict(False, f"移動先に不正なパス要素があります: {part}")
        if path_has_link(dest.parent, stop_at=organize_root) or is_link_or_junction(Path(organize_root)):
            return SafetyVerdict(False, "移動先にリンク/ジャンクションが含まれます")
        if dest.exists():
            return SafetyVerdict(False, "移動先に同名ファイルが存在します（上書き禁止）")
        return SafetyVerdict(True)

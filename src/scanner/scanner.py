"""監視対象フォルダの走査。サブフォルダは明示的に有効化した場合のみ辿る。"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from safety.rules import SafetyChecker, is_link_or_junction


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    size: int
    mtime: float

    @classmethod
    def of(cls, path: Path) -> "FileSnapshot":
        st = os.stat(path)
        return cls(str(path), st.st_size, st.st_mtime)


def iter_files(root: Path, recursive: bool, safety: SafetyChecker) -> Iterator[Path]:
    root = Path(root)
    if not root.is_dir() or is_link_or_junction(root):
        return
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            p = Path(e.path)
            try:
                if e.is_symlink() or is_link_or_junction(p):
                    continue
                if e.is_dir(follow_symlinks=False):
                    if recursive and not safety.is_dir_excluded(p):
                        stack.append(p)
                    continue
                if e.is_file(follow_symlinks=False):
                    yield p
            except OSError:
                continue


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

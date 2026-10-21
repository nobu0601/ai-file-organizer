"""上書きしない安全なファイル移動と取り消し。"""
from __future__ import annotations

import errno
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from history.db import HistoryDB, Operation
from safety.rules import SafetyChecker, is_link_or_junction, safe_component
from scanner.scanner import FileSnapshot, sha256_of

log = logging.getLogger(__name__)


class MoveError(RuntimeError):
    pass


@dataclass
class MoveOutcome:
    ok: bool
    src: str
    dest: str | None
    op_id: int | None
    message: str


def unique_destination(directory: Path, name: str) -> Path:
    name = safe_component(name, max_len=150)
    cand = directory / name
    if not cand.exists():
        return cand
    stem, suffix = Path(name).stem, Path(name).suffix
    for i in range(1, 10000):
        cand = directory / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    raise MoveError("衝突しない移動先名を決められません")


def _copy_exclusive(src: Path, dest: Path) -> None:
    """dest を新規作成（既存なら失敗）してコピーし、内容一致を確認してから元を消す。"""
    src_hash = sha256_of(src)
    try:
        with open(src, "rb") as fi, open(dest, "xb") as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
        shutil.copystat(src, dest)
        if sha256_of(dest) != src_hash:
            raise MoveError("コピー後の内容が一致しません")
    except BaseException:
        try:
            if dest.exists():
                os.unlink(dest)
        except OSError:
            pass
        raise
    os.unlink(src)


def move_no_overwrite(src: Path, dest: Path) -> None:
    """dest が存在する場合は FileExistsError。決して上書きしない。"""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(errno.EEXIST, "移動先が既に存在します", str(dest))
    if os.name == "nt":
        try:
            os.rename(src, dest)   # Windows の rename は既存ファイルを上書きしない
            return
        except FileExistsError:
            raise
        except OSError as e:
            if getattr(e, "winerror", None) != 17:  # ERROR_NOT_SAME_DEVICE 以外はそのまま失敗
                raise
        _copy_exclusive(src, dest)
        return
    try:
        os.link(src, dest)     # 既存なら EEXIST で失敗するため原子的に上書きを防げる
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.EACCES):
            _copy_exclusive(src, dest)
            return
        raise
    try:
        os.unlink(src)
    except OSError:
        os.unlink(dest)
        raise


class Mover:
    def __init__(self, db: HistoryDB, safety: SafetyChecker, use_sha256: bool = True) -> None:
        self.db = db
        self.safety = safety
        self.use_sha256 = use_sha256

    def move(self, src: Path, dest_dir: Path, organize_root: Path, *, expected: FileSnapshot | None,
             category_id: str | None, category_name: str | None, reason: str) -> MoveOutcome:
        src = Path(src)
        try:
            if not src.is_file():
                raise MoveError("移動元のファイルが見つかりません")
            if is_link_or_junction(src):
                raise MoveError("リンクは移動しません")
            snap = FileSnapshot.of(src)
            if expected is not None and (snap.size != expected.size or abs(snap.mtime - expected.mtime) > 1e-6):
                raise MoveError("分類後にファイルが変更されました。再分析が必要です")
            digest = sha256_of(src) if self.use_sha256 else None
            last_err: Exception | None = None
            for _ in range(5):
                dest = unique_destination(Path(dest_dir), src.name)
                verdict = self.safety.check_destination(dest, organize_root)
                if not verdict.allowed:
                    raise MoveError(verdict.reason)
                try:
                    move_no_overwrite(src, dest)
                    break
                except FileExistsError as e:   # 直前に別ファイルが作られた: 別名で再試行
                    last_err = e
                    continue
            else:
                raise MoveError(f"移動先の名前衝突を解決できません: {last_err}")
            op_id = self.db.add_operation(
                src_path=str(src), dest_path=str(dest), size=snap.size, mtime=snap.mtime, sha256=digest,
                category_id=category_id, category_name=category_name, reason=reason, result="moved")
            return MoveOutcome(True, str(src), str(dest), op_id, "移動しました")
        except (MoveError, OSError) as e:
            msg = str(e)
            op_id = self.db.add_operation(
                src_path=str(src), dest_path=None, size=None, mtime=None, sha256=None,
                category_id=category_id, category_name=category_name, reason=reason, result="failed", error=msg)
            log.warning("移動失敗 %s: %s", src, msg)
            return MoveOutcome(False, str(src), None, op_id, msg)

    def can_undo(self, op: Operation) -> tuple[bool, str]:
        if op.result != "moved" or not op.dest_path:
            return False, "取り消し可能な移動操作ではありません"
        dest, src = Path(op.dest_path), Path(op.src_path)
        if not dest.exists():
            return False, "移動先のファイルが見つかりません（削除または移動された可能性）"
        if is_link_or_junction(dest):
            return False, "移動先がリンクに置き換わっています"
        if src.exists() or src.is_symlink():
            return False, "元の場所に同名の別ファイルがあります（上書きしません）"
        try:
            st = os.stat(dest)
        except OSError as e:
            return False, f"移動先を確認できません: {e}"
        if op.size is not None and st.st_size != op.size:
            return False, "移動後にファイル内容が変更されています"
        if op.sha256:
            try:
                if sha256_of(dest) != op.sha256:
                    return False, "移動後にファイル内容が変更されています（ハッシュ不一致）"
            except OSError as e:
                return False, f"ハッシュを計算できません: {e}"
        elif op.mtime is not None and abs(st.st_mtime - op.mtime) > 1e-6:
            return False, "移動後にファイルが更新されています"
        return True, ""

    def undo(self, op_id: int) -> MoveOutcome:
        op = self.db.get_operation(op_id)
        if op is None:
            return MoveOutcome(False, "", None, op_id, "操作が見つかりません")
        ok, why = self.can_undo(op)
        if not ok:
            self.db.mark_undo_failed(op_id, why)
            return MoveOutcome(False, op.src_path, op.dest_path, op_id, why)
        try:
            move_no_overwrite(Path(op.dest_path), Path(op.src_path))
        except FileExistsError:
            why = "元の場所に同名の別ファイルがあります（上書きしません）"
            self.db.mark_undo_failed(op_id, why)
            return MoveOutcome(False, op.src_path, op.dest_path, op_id, why)
        except OSError as e:
            why = f"復元に失敗しました: {e}"
            self.db.mark_undo_failed(op_id, why)
            return MoveOutcome(False, op.src_path, op.dest_path, op_id, why)
        self.db.mark_undone(op_id)
        return MoveOutcome(True, op.src_path, op.dest_path, op_id, "元の場所に戻しました")

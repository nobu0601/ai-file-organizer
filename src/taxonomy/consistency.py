"""実フォルダと folder_map.json の整合性チェック。

不一致を見つけても削除・改名は自動で行わず、修正案だけを返す。
安全に自動適用できるのは「不足フォルダの作成」と「改名先が空いている場合のフォルダ改名」のみ。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from safety.rules import is_link_or_junction, safe_relative_path
from taxonomy.model import Taxonomy


@dataclass
class Mismatch:
    kind: str                 # missing_folder / unknown_folder / inactive_category_folder / renamed_folder / link_in_tree
    path: str                 # 整理先からの相対パス
    category_id: str | None
    message: str
    suggestion: str
    fix: dict | None = None   # 安全に適用できる修正（なければ None）


def check_consistency(tax: Taxonomy, organize_root: Path, history_paths: dict[str, list[str]] | None = None
                      ) -> list[Mismatch]:
    """history_paths: category_id -> そのカテゴリが過去に使っていた相対パス（改名検出用）"""
    root = Path(organize_root)
    out: list[Mismatch] = []
    expected = {c.relative_path.lower(): c for c in tax.active()}
    inactive = {c.relative_path.lower(): c for c in tax.categories.values() if not c.active}

    actual: dict[str, str] = {}
    if root.is_dir():
        for dirpath, dirnames, _ in os.walk(root):
            keep = []
            for d in sorted(dirnames):
                full = Path(dirpath) / d
                rel = full.relative_to(root).as_posix()
                if is_link_or_junction(full):
                    out.append(Mismatch("link_in_tree", rel, None,
                                        "整理先にリンク/ジャンクションがあります",
                                        "リンクは辿りません。必要なら手動で確認してください"))
                    continue
                actual[rel.lower()] = rel
                keep.append(d)
            dirnames[:] = keep

    for key, c in expected.items():
        if key in actual:
            continue
        old = _find_old_path(c.category_id, history_paths or {}, actual)
        if old:
            out.append(Mismatch(
                "renamed_folder", c.relative_path, c.category_id,
                f"カテゴリ「{c.category_name}」の名前変更が実フォルダに反映されていません（現在: {old}）",
                f"フォルダ {old} を {c.relative_path} に改名する",
                fix={"action": "rename_folder", "from": old, "to": c.relative_path},
            ))
        else:
            out.append(Mismatch(
                "missing_folder", c.relative_path, c.category_id,
                f"カテゴリ「{c.category_name}」のフォルダがありません",
                f"フォルダ {c.relative_path} を作成する（ファイル移動時にも自動作成されます）",
                fix={"action": "create_folder", "to": c.relative_path},
            ))

    renamed_from = {m.fix["from"].lower() for m in out if m.fix and m.fix.get("action") == "rename_folder"}
    for key, rel in sorted(actual.items()):
        if key in expected or key in renamed_from:
            continue
        # 有効カテゴリの祖先フォルダは問題なし
        if any(e.startswith(key + "/") for e in expected):
            continue
        if any(key.startswith(r + "/") for r in renamed_from):
            continue
        if key in inactive:
            c = inactive[key]
            target = tax.get(c.merged_into)
            sug = (f"中身を {target.relative_path} へ移すか確認してください" if target
                   else "不要であれば手動で整理してください")
            out.append(Mismatch("inactive_category_folder", rel, c.category_id,
                                f"統合/アーカイブ済みカテゴリ「{c.category_name}」のフォルダが残っています", sug))
            continue
        # 未登録フォルダの子は親だけ報告する（親が既知のフォルダか整理先直下のときだけ報告）
        parent_key = key.rsplit("/", 1)[0] if "/" in key else ""
        parent_known = (parent_key == "" or parent_key in expected
                        or any(e.startswith(parent_key + "/") for e in expected))
        if not parent_known:
            continue
        out.append(Mismatch("unknown_folder", rel, None,
                            "分類体系に登録されていないフォルダです",
                            "カテゴリとして追加するか、整理先の外へ移すか確認してください（自動では変更しません）"))
    return out


def _find_old_path(cid: str, history_paths: dict[str, list[str]], actual: dict[str, str]) -> str | None:
    for old in reversed(history_paths.get(cid, [])):
        if old.lower() in actual:
            return actual[old.lower()]
    return None


def apply_fix(fix: dict, organize_root: Path) -> str:
    """安全な修正だけを適用する。上書き・削除は行わない。"""
    root = Path(organize_root)
    action = fix.get("action")
    to_rel = safe_relative_path(fix.get("to", ""))
    if not to_rel:
        raise ValueError("修正先のパスが不正です")
    dest = root / to_rel
    if action == "create_folder":
        dest.mkdir(parents=True, exist_ok=True)
        return f"作成しました: {to_rel}"
    if action == "rename_folder":
        src = root / safe_relative_path(fix.get("from", ""))
        if not src.is_dir() or is_link_or_junction(src):
            raise ValueError("改名元のフォルダが見つかりません")
        if dest.exists():
            raise ValueError("改名先に同名のフォルダが既にあります（上書きしません）")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dest)
        return f"改名しました: {fix.get('from')} → {to_rel}"
    raise ValueError(f"未対応の修正です: {action}")


def collect_history_paths(history_snapshots: list[dict]) -> dict[str, list[str]]:
    """folder_map_history の after スナップショットから category_id ごとの過去パスを集める。"""
    out: dict[str, list[str]] = {}
    for snap in history_snapshots:
        for c in snap.get("categories", []):
            lst = out.setdefault(c["category_id"], [])
            if c.get("relative_path") and c["relative_path"] not in lst:
                lst.append(c["relative_path"])
    return out

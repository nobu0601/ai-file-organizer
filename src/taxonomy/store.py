"""正式な分類体系の保存（folder_map.json / folder_map.md / folder_map_history/）。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from settings.config import atomic_write_text
from settings.paths import AppPaths
from taxonomy.export import render_markdown
from taxonomy.model import Taxonomy, apply_operations, now_iso
from taxonomy.schema import validate_folder_map

log = logging.getLogger(__name__)


class TaxonomyStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self._lock = threading.RLock()
        self.taxonomy = self._load()

    def _load(self) -> Taxonomy:
        p = self.paths.folder_map_json
        if not p.exists():
            return Taxonomy()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            validate_folder_map(data)
            return Taxonomy.from_dict(data)
        except Exception as e:
            # 壊れたファイルは上書きせず退避し、読み込み失敗をログに残す
            backup = p.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
            try:
                p.replace(backup)
            except OSError:
                pass
            log.error("folder_map.json の読み込みに失敗しました (%s)。%s に退避しました", e, backup)
            return Taxonomy()

    def reload(self) -> Taxonomy:
        with self._lock:
            self.taxonomy = self._load()
            return self.taxonomy

    def apply(self, operations: list[dict], reason: str, pending_proposals: list[str] | None = None) -> bool:
        """承認済みの変更を正式体系に反映する。内容が変わらなければ何も書かず False。"""
        with self._lock:
            before = self.taxonomy
            after = apply_operations(before, operations)
            if after.content_signature() == before.content_signature():
                return False
            after.taxonomy_version = before.taxonomy_version + 1
            after.updated_at = now_iso()
            if before.taxonomy_version == 0 and not before.categories:
                after.generated_at = after.updated_at
            data = after.to_dict()
            validate_folder_map(data)
            self._write_history(before, after, operations, reason)
            atomic_write_text(self.paths.folder_map_json, json.dumps(data, ensure_ascii=False, indent=2))
            self.taxonomy = after
            self.export_markdown(pending_proposals or [])
            return True

    def export_markdown(self, pending_proposals: list[str]) -> None:
        with self._lock:
            text = render_markdown(self.taxonomy, pending_proposals)
            p = self.paths.folder_map_md
            if p.exists() and p.read_text(encoding="utf-8") == text:
                return
            atomic_write_text(p, text)

    def ensure_outputs(self, pending_proposals: list[str]) -> None:
        """出力ファイルが無ければ作る（あれば内容が同じ限り書き換えない）。"""
        with self._lock:
            if not self.paths.folder_map_json.exists():
                data = self.taxonomy.to_dict()
                validate_folder_map(data)
                atomic_write_text(self.paths.folder_map_json, json.dumps(data, ensure_ascii=False, indent=2))
            self.export_markdown(pending_proposals)

    def _write_history(self, before: Taxonomy, after: Taxonomy, operations: list[dict], reason: str) -> None:
        self.paths.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        entry = {
            "from_version": before.taxonomy_version,
            "to_version": after.taxonomy_version,
            "changed_at": after.updated_at,
            "reason": reason,
            "operations": operations,
            "operation_types": sorted({o.get("op", "") for o in operations}),
            "before": before.to_dict(),
            "after": after.to_dict(),
        }
        path = self.paths.history_dir / f"v{after.taxonomy_version:04d}_{stamp}.json"
        atomic_write_text(path, json.dumps(entry, ensure_ascii=False, indent=2))

    def history_snapshots(self) -> list[dict]:
        out = []
        for p in sorted(self.paths.history_dir.glob("v*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                for key in ("before", "after"):
                    if isinstance(d.get(key), dict):
                        out.append(d[key])
            except (OSError, ValueError):
                continue
        return out

    def history_entries(self) -> list[dict]:
        out = []
        for p in sorted(self.paths.history_dir.glob("v*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"file": p.name, "from_version": d.get("from_version"),
                            "to_version": d.get("to_version"), "changed_at": d.get("changed_at"),
                            "reason": d.get("reason"), "operation_types": d.get("operation_types", [])})
            except (OSError, ValueError):
                continue
        return out

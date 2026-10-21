"""アプリの中核。GUI から独立しており、テストからも直接使える。

処理の流れ（1 ファイル）:
  安全チェック → 内容解析 → 分類（AI/内容ベース） → 検証 → 自動移動 or 実行待ち or 要確認
分類と移動は別の段階であり、移動は必ず「承認済みカテゴリ」「安全条件」「設定」を満たす場合だけ行う。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from classifier.base import ClassificationResult, Classifier, DocInput
from classifier.heuristic import HeuristicClassifier
from classifier.model_select import recommended
from classifier.ollama import OllamaClassifier, OllamaClient, OllamaError
from classifier.semantic import EMBED_MODEL
from classifier.validation import evaluate
from history.db import (F_FAILED, F_MOVED, F_PENDING, F_PROCESSING, F_READY, F_REVIEW, F_SKIPPED, HistoryDB,
                        path_key)
from organizer.mover import Mover, MoveOutcome
from safety.rules import SafetyChecker, is_within
from scanner.features import build_features
from scanner.parsers import ContentExtractor
from scanner.scanner import FileSnapshot, iter_files
from settings.config import Settings, WatchFolder, load_settings, save_settings
from settings.paths import AppPaths
from settings.system import total_memory_gb
from taxonomy.consistency import Mismatch, apply_fix, check_consistency, collect_history_paths
from taxonomy.model import Taxonomy, TaxonomyError, new_category_id
from taxonomy.store import TaxonomyStore
from watcher.monitor import FolderMonitor
from watcher.queue import Q_DONE, Q_FAILED, Q_READY, Q_REVIEW, Q_SKIPPED, ProcessingQueue

log = logging.getLogger(__name__)

FINAL_STATUSES = {F_READY, F_REVIEW, F_MOVED, F_SKIPPED}
QUEUE_STATUS = {F_MOVED: Q_DONE, F_READY: Q_READY, F_REVIEW: Q_REVIEW, F_SKIPPED: Q_SKIPPED, F_FAILED: Q_FAILED}


@dataclass
class ProcessOutcome:
    path: str
    status: str
    message: str
    category_id: str | None = None
    confidence: float | None = None
    dest: str | None = None


class Engine:
    def __init__(self, paths: AppPaths | None = None, settings: Settings | None = None,
                 classifier: Classifier | None = None) -> None:
        self.paths = (paths or AppPaths()).ensure()
        self.settings = settings or load_settings(self.paths.settings_file)
        self.db = HistoryDB(self.paths.database)
        self.store = TaxonomyStore(self.paths)
        self.heuristic = HeuristicClassifier()
        self._classifier_override = classifier
        self._listeners: list[Callable[[dict], None]] = []
        self._proc_lock = threading.Lock()
        self._in_progress: set[str] = set()
        self._worker: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self.paused = False
        self.monitor = FolderMonitor(self._on_fs_event)
        self.rebuild()
        self.store.ensure_outputs(self.pending_proposal_titles())

    # ---------- 構成 ----------
    def rebuild(self) -> None:
        s = self.settings
        self.organize_root = Path(s.organize_root)
        self.safety = SafetyChecker(s, protected_roots=[self.paths.root, self.organize_root])
        self.extractor = ContentExtractor(s.max_parse_bytes, s.max_text_chars)
        self.mover = Mover(self.db, self.safety, s.use_sha256)
        if not hasattr(self, "queue"):
            self.queue = ProcessingQueue(s.stability_checks, s.stability_quiet_seconds, s.max_retries)
        else:
            self.queue.checks = s.stability_checks
            self.queue.quiet_seconds = s.stability_quiet_seconds
            self.queue.max_attempts = s.max_retries

        self._llm: Classifier | None = None
        o = s.ollama
        if o.enabled:
            try:
                self._llm = OllamaClassifier(OllamaClient(o.base_url, o.model, o.timeout_seconds), self.heuristic)
            except OllamaError as e:
                log.warning("Ollama 設定が無効です: %s", e)

    @property
    def classifier(self) -> Classifier:
        if self._classifier_override is not None:
            return self._classifier_override
        return self._llm or self.heuristic

    def _client(self) -> OllamaClient:
        o = self.settings.ollama
        return OllamaClient(o.base_url, o.model, o.timeout_seconds)

    def test_ollama(self) -> tuple[bool, str]:
        try:
            return self._client().available()
        except OllamaError as e:
            return False, str(e)

    def recommended_model(self) -> tuple[str, float, float | None]:
        """(推奨モデル名, ダウンロードサイズ GB, この PC のメモリ GB)"""
        ram = total_memory_gb()
        c = recommended(ram)
        return c.name, c.size_gb, ram

    def pull_model(self, name: str) -> str:
        """分類用モデルと、ファイルを意味でまとめるための埋め込みモデルをダウンロードする。"""
        client = self._client()
        client.pull(name)
        client.pull(EMBED_MODEL)
        self.rebuild()   # 自動選択をやり直す
        return f"{name} と {EMBED_MODEL} をダウンロードしました"

    def save_settings(self) -> None:
        save_settings(self.settings, self.paths.settings_file)
        self.rebuild()

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, **event) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                log.exception("listener error")

    # ---------- 監視対象 ----------
    def add_watch_folder(self, path: str, recursive: bool = False) -> None:
        p = Path(path).resolve()
        if not p.is_dir():
            raise ValueError("フォルダが見つかりません")
        if is_within(p, self.organize_root) or (recursive and is_within(self.organize_root, p)):
            raise ValueError("整理先フォルダ（またはそれを含むフォルダの再帰監視）は監視対象にできません")
        if is_within(p, self.paths.root):
            raise ValueError("アプリの管理用データ領域は監視対象にできません")
        if any(Path(w.path) == p for w in self.settings.watch_folders):
            raise ValueError("既に登録されています")
        self.settings.watch_folders.append(WatchFolder(str(p), recursive))
        self.save_settings()

    def remove_watch_folder(self, path: str) -> None:
        self.settings.watch_folders = [w for w in self.settings.watch_folders if w.path != path]
        self.save_settings()

    def _watch_root_for(self, path: str) -> WatchFolder | None:
        best = None
        for w in self.settings.watch_folders:
            if is_within(path, w.path):
                direct = os.path.normcase(str(Path(path).parent)) == os.path.normcase(str(Path(w.path)))
                if (w.recursive or direct) and (best is None or len(w.path) > len(best.path)):
                    best = w
        return best

    # ---------- 監視の開始・停止 ----------
    def start_monitoring(self) -> list[str]:
        errors = self.monitor.start([(w.path, w.recursive) for w in self.settings.watch_folders])
        self.paused = False
        self._ensure_worker()
        self._emit(type="monitor", running=True)
        return errors

    def stop_monitoring(self) -> None:
        self.monitor.stop()
        self._emit(type="monitor", running=False)

    def pause(self) -> None:
        self.paused = True
        self._emit(type="monitor", paused=True)

    def resume(self) -> None:
        self.paused = False
        self._ensure_worker()
        self._emit(type="monitor", paused=False)

    def mark_seen(self) -> None:
        """この時刻までのファイルは検出済み。次回起動時、これより新しいファイルを「停止中の新規」とみなす。"""
        self.db.set_state("last_seen_at", repr(time.time()))

    def shutdown(self) -> None:
        if self.monitor.running:
            self.mark_seen()
        self.monitor.stop()
        self._stop_evt.set()
        if self._worker:
            self._worker.join(timeout=5)
        self.db.close()

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_evt.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="organizer-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            if not self.paused:
                try:
                    self.process_queue_once()
                except Exception:
                    log.exception("キュー処理でエラー")
            self._stop_evt.wait(self.settings.stability_poll_seconds)

    def _on_fs_event(self, path: str, root: str) -> None:
        if self.should_track(path):
            if self.queue.enqueue(path, "event", root):
                self._emit(type="queue")

    def should_track(self, path: str) -> bool:
        p = Path(path)
        w = self._watch_root_for(str(p))
        if w is None:
            return False
        if not self.safety.check_source(p, Path(w.path)).allowed:
            return False
        return True

    # ---------- キュー ----------
    def process_queue_once(self, now: float | None = None) -> list[ProcessOutcome]:
        out = []
        for item in self.queue.poll_stability(now):
            res = self.process_file(item.path, item.origin)
            self.queue.finish(item.path, QUEUE_STATUS.get(res.status, Q_DONE), res.message)
            out.append(res)
            self._emit(type="processed", path=item.path, status=res.status, message=res.message)
        if out:
            self._emit(type="queue")
        return out

    def scan_now(self, origin: str = "scan") -> int:
        """監視対象を走査し、未処理または変更されたファイルをキューへ入れる。"""
        n = 0
        since = float(self.db.get_state("last_seen_at", "0") or 0)
        for w in self.settings.watch_folders:
            for p in iter_files(Path(w.path), w.recursive, self.safety):
                if not self.safety.check_source(p, Path(w.path)).allowed:
                    continue
                if self._already_handled(p):
                    continue
                o = origin
                if origin == "startup":
                    # 停止中に追加された新規ファイルは監視イベントと同等に扱う。それ以外は初回整理（プレビュー）
                    try:
                        o = "startup" if since and os.stat(p).st_mtime > since else "scan"
                    except OSError:
                        continue
                if self.queue.enqueue(p, o, w.path):
                    n += 1
        self.mark_seen()
        self._ensure_worker()
        self._emit(type="queue")
        return n

    def _already_handled(self, p: Path) -> bool:
        rec = self.db.get_file(p)
        if rec is None or rec.status not in FINAL_STATUSES:
            return False
        try:
            snap = FileSnapshot.of(p)
        except OSError:
            return True
        return rec.size == snap.size and rec.mtime is not None and abs(rec.mtime - snap.mtime) < 1e-6

    # ---------- 1 ファイルの処理 ----------
    def analyze(self, path: Path, watch_root: Path | None) -> DocInput:
        parse = self.extractor.extract(path)
        st = os.stat(path)
        return DocInput(path=Path(path), features=build_features(Path(path), parse.text, watch_root),
                        parse=parse, size=st.st_size, mtime=st.st_mtime)

    def process_file(self, path: str | Path, origin: str = "event") -> ProcessOutcome:
        p = Path(path)
        key = path_key(p)
        with self._proc_lock:
            if key in self._in_progress:
                return ProcessOutcome(str(p), F_SKIPPED, "既に処理中です（二重処理を防止）")
            self._in_progress.add(key)
        try:
            return self._process(p, origin)
        except Exception as e:
            log.exception("処理に失敗: %s", p)
            self.db.upsert_file(str(p), status=F_FAILED, reason=f"{type(e).__name__}: {e}", origin=origin)
            return ProcessOutcome(str(p), F_FAILED, f"処理に失敗しました: {e}")
        finally:
            with self._proc_lock:
                self._in_progress.discard(key)

    def _process(self, p: Path, origin: str) -> ProcessOutcome:
        if not p.is_file():
            return ProcessOutcome(str(p), F_SKIPPED, "ファイルが存在しません")
        if self._already_handled(p):
            rec = self.db.get_file(p)
            return ProcessOutcome(str(p), rec.status if rec else F_SKIPPED, "処理済みのため再処理しません")
        w = self._watch_root_for(str(p))
        root = Path(w.path) if w else None
        verdict = self.safety.check_source(p, root)
        snap = FileSnapshot.of(p)
        if not verdict.allowed:
            self.db.upsert_file(str(p), status=F_SKIPPED, size=snap.size, mtime=snap.mtime,
                                reason=verdict.reason, origin=origin)
            return ProcessOutcome(str(p), F_SKIPPED, verdict.reason)
        if not self.settings.auto_classify:
            self.db.upsert_file(str(p), status=F_PENDING, size=snap.size, mtime=snap.mtime,
                                reason="自動分類が無効です", origin=origin)
            return ProcessOutcome(str(p), F_REVIEW, "自動分類が無効のため待機します")
        self.db.upsert_file(str(p), status=F_PROCESSING, size=snap.size, mtime=snap.mtime, origin=origin)
        doc = self.analyze(p, root)
        tax = self.store.taxonomy
        result = self.classifier.classify(doc, tax)
        project_root = self.safety.find_project_root(p, root)
        sensitive = self.safety.sensitive_reason(p, doc.parse.text)
        decision = evaluate(result, tax, doc.parse, self.settings.confidence_threshold, sensitive,
                            str(project_root) if project_root else None)
        proposal_id = None
        if result.new_category is not None and result.category_id is None and doc.parse.readable \
                and not sensitive and not project_root:
            proposal_id = self._propose_new_category(result, str(p))
        cat = tax.get(result.category_id)
        detail = {
            "reasons": result.reasons, "decision": decision.reasons, "matched": result.matched_keywords,
            "source": result.source, "parse_status": doc.parse.status, "proposal_id": proposal_id,
            "suggested_name": cat.category_name if cat else None,
            "new_category": result.new_category.name if result.new_category else None,
        }
        reason = result.reason_text
        if not decision.auto_ok:
            self.db.upsert_file(str(p), status=F_REVIEW, size=snap.size, mtime=snap.mtime,
                                category_id=result.category_id, confidence=result.confidence,
                                reason=decision.reason_text, detail=detail, origin=origin)
            return ProcessOutcome(str(p), F_REVIEW, decision.reason_text, result.category_id, result.confidence)
        can_auto = (self.settings.auto_move and not self.settings.preview_mode
                    and origin in ("event", "startup"))
        if not can_auto:
            why = ("プレビューモードのため実行待ち" if self.settings.preview_mode else
                   "自動移動が無効のため実行待ち" if not self.settings.auto_move else
                   "既存ファイルの初回整理はプレビュー後に実行します")
            self.db.upsert_file(str(p), status=F_READY, size=snap.size, mtime=snap.mtime,
                                category_id=result.category_id, confidence=result.confidence,
                                reason=reason, detail=detail | {"waiting": why}, origin=origin)
            return ProcessOutcome(str(p), F_READY, why, result.category_id, result.confidence)
        mv = self._move_to_category(p, cat, snap, reason)
        if mv.ok:
            return ProcessOutcome(str(p), F_MOVED, f"自動移動しました → {mv.dest}", cat.category_id,
                                  result.confidence, mv.dest)
        self.db.upsert_file(str(p), status=F_FAILED, size=snap.size, mtime=snap.mtime,
                            category_id=result.category_id, confidence=result.confidence,
                            reason=mv.message, detail=detail, origin=origin)
        return ProcessOutcome(str(p), F_FAILED, mv.message, cat.category_id, result.confidence)

    def _move_to_category(self, p: Path, cat, snap: FileSnapshot | None, reason: str) -> MoveOutcome:
        dest_dir = self.organize_root.joinpath(*cat.relative_path.split("/"))
        mv = self.mover.move(p, dest_dir, self.organize_root, expected=snap, category_id=cat.category_id,
                             category_name=cat.category_name, reason=reason)
        if mv.ok:
            self.db.delete_file(p)
            dsnap = FileSnapshot.of(Path(mv.dest))
            self.db.upsert_file(mv.dest, status=F_MOVED, size=dsnap.size, mtime=dsnap.mtime,
                                category_id=cat.category_id, reason=reason, origin="moved")
            self._emit(type="moved", src=str(p), dest=mv.dest)
        return mv

    # ---------- 手動操作 ----------
    def execute_ready(self, paths: list[str] | None = None) -> list[MoveOutcome]:
        """プレビューで確認した「実行待ち」ファイルを移動する。実行時に条件を再検証する。"""
        targets = [r for r in self.db.list_files((F_READY,)) if paths is None or r.path in paths]
        out = []
        for rec in targets:
            p = Path(rec.path)
            cat = self.store.taxonomy.get(rec.category_id)
            if cat is None or not cat.active:
                self.db.upsert_file(rec.path, status=F_REVIEW, reason="カテゴリが承認済みではなくなりました",
                                    size=rec.size, mtime=rec.mtime)
                continue
            w = self._watch_root_for(rec.path)
            verdict = self.safety.check_source(p, Path(w.path) if w else None)
            if not verdict.allowed:
                self.db.upsert_file(rec.path, status=F_SKIPPED, reason=verdict.reason, size=rec.size, mtime=rec.mtime)
                continue
            snap = FileSnapshot(rec.path, rec.size or 0, rec.mtime or 0.0)
            mv = self._move_to_category(p, cat, snap, rec.reason or "プレビュー確認後に実行")
            if not mv.ok:
                self.db.upsert_file(rec.path, status=F_FAILED, reason=mv.message, size=rec.size, mtime=rec.mtime,
                                    category_id=rec.category_id)
            out.append(mv)
        self._emit(type="history")
        return out

    def move_manually(self, path: str, category_id: str, note: str = "ユーザーが手動でカテゴリを指定") -> MoveOutcome:
        cat = self.store.taxonomy.get(category_id)
        if cat is None or not cat.active:
            return MoveOutcome(False, path, None, None, "承認済みのカテゴリを指定してください")
        p = Path(path)
        w = self._watch_root_for(path)
        verdict = self.safety.check_source(p, Path(w.path) if w else None)
        if not verdict.allowed:
            return MoveOutcome(False, path, None, None, verdict.reason)
        mv = self._move_to_category(p, cat, None, note)
        self._emit(type="history")
        return mv

    def dismiss(self, path: str, reason: str = "ユーザーが対象外に設定") -> None:
        try:
            snap = FileSnapshot.of(Path(path))
            self.db.upsert_file(path, status=F_SKIPPED, size=snap.size, mtime=snap.mtime, reason=reason)
        except OSError:
            self.db.delete_file(path)

    def reanalyze(self, path: str) -> ProcessOutcome:
        rec = self.db.get_file(path)
        origin = rec.origin if rec else "scan"
        self.db.delete_file(path)
        return self.process_file(path, origin or "scan")

    def undo(self, op_id: int) -> MoveOutcome:
        mv = self.mover.undo(op_id)
        if mv.ok:
            self.db.delete_file(mv.dest)
            snap = FileSnapshot.of(Path(mv.src))
            # 元の場所に戻ったファイルを監視が再分類・再移動しないよう記録する
            self.db.upsert_file(mv.src, status=F_SKIPPED, size=snap.size, mtime=snap.mtime,
                                reason="整理を取り消したファイル（再処理しません）")
        self._emit(type="history")
        return mv

    # ---------- 分類体系 ----------
    def pending_proposal_titles(self) -> list[str]:
        return [f"{p.title}（{p.reason}）" for p in self.db.list_proposals("pending")]

    def _refresh_outputs(self) -> None:
        self.store.export_markdown(self.pending_proposal_titles())

    def _propose_new_category(self, result: ClassificationResult, path: str) -> str:
        sug = result.new_category
        for prop in self.db.list_proposals("pending"):
            if prop.kind != "add" or len(prop.operations) != 1:
                continue
            spec = prop.operations[0]["category"]
            kws = set(spec.get("classification_rules", {}).get("keywords", []))
            overlap = len(kws & set(sug.keywords)) / max(1, min(len(kws), len(sug.keywords)))
            if overlap >= 0.5 or spec.get("category_name", "").lower() == sug.name.lower():
                if path not in prop.related_files:
                    self.db.update_proposal(prop.id, related_files=prop.related_files + [path])
                return prop.id
        spec = {
            "category_id": new_category_id(), "category_name": sug.name, "parent_category_id": sug.parent_category_id,
            "description": sug.description,
            "classification_rules": {"keywords": sug.keywords, "keyword_weights": sug.keyword_weights},
            "examples": sug.examples,
        }
        pid = self.db.add_proposal(kind="add", title=f"新規カテゴリ「{sug.name}」",
                                   reason="既存カテゴリに適合しないファイルが追加されたため",
                                   operations=[{"op": "add", "category": spec}], related_files=[path])
        self._refresh_outputs()
        self._emit(type="taxonomy")
        return pid

    def collect_docs(self) -> list[DocInput]:
        docs = []
        for w in self.settings.watch_folders:
            root = Path(w.path)
            for p in iter_files(root, w.recursive, self.safety):
                if not self.safety.check_source(p, root).allowed:
                    continue
                if self.safety.find_project_root(p, root):
                    continue
                try:
                    d = self.analyze(p, root)
                except OSError:
                    continue
                if not d.parse.readable or self.safety.sensitive_reason(p, d.parse.text):
                    continue
                docs.append(d)
        return docs

    def propose_taxonomy(self) -> dict:
        """監視対象のファイル群を分析し、分類体系の案を「提案」として保存する（正式体系には反映しない）。"""
        docs = self.collect_docs()
        draft = self.classifier.propose_taxonomy(docs, self.store.taxonomy)
        # 古い未承認の初回提案は置き換える
        for prop in self.db.list_proposals("pending"):
            if prop.source in ("ai-initial",):
                self.db.set_proposal_status(prop.id, "superseded")
        created = []
        for c in draft.new_categories:
            parent = next((x for x in draft.new_categories if x.category_id == c.parent_category_id), None)
            title = f"新規カテゴリ「{c.name}」" + (f"（「{parent.name}」の下位）" if parent else "")
            reason = f"{len(c.members)}件のファイルに共通するテーマ: {', '.join(c.keywords[:4])}"
            pid = self.db.add_proposal(kind="add", title=title, reason=reason,
                                       operations=[{"op": "add", "category": c.to_spec()}],
                                       related_files=c.members, source="ai-initial")
            created.append(pid)
        self._refresh_outputs()
        self._emit(type="taxonomy")
        return {"proposals": created, "analyzed": len(docs), "reused": len(draft.reused),
                "unassigned": len(draft.unassigned), "notes": draft.notes, "source": draft.source}

    def approve_proposals(self, proposal_ids: list[str]) -> tuple[bool, str]:
        props = [p for p in (self.db.get_proposal(i) for i in proposal_ids) if p and p.status == "pending"]
        if not props:
            return False, "承認できる提案がありません"
        ops = [op for p in props for op in p.operations]
        reason = " / ".join(f"{p.title}: {p.reason}" for p in props)
        try:
            changed = self.store.apply(ops, reason=reason)
        except TaxonomyError as e:
            return False, f"承認できません: {e}"
        for p in props:
            self.db.set_proposal_status(p.id, "approved")
        self._refresh_outputs()
        # 関連ファイルを再分類する（承認されたカテゴリで改めて判定）
        related = {f for p in props for f in p.related_files}
        for f in related:
            rec = self.db.get_file(f)
            if Path(f).is_file() and (rec is None or rec.status in (F_REVIEW, F_PENDING, F_FAILED)):
                self.db.delete_file(f)
                self.process_file(f, rec.origin if rec and rec.origin else "scan")
        self._emit(type="taxonomy")
        return True, ("分類体系を更新しました" if changed else "変更はありませんでした")

    def reject_proposals(self, proposal_ids: list[str]) -> None:
        for pid in proposal_ids:
            self.db.set_proposal_status(pid, "rejected")
        self._refresh_outputs()
        self._emit(type="taxonomy")

    def change_taxonomy(self, operations: list[dict], reason: str) -> tuple[bool, str]:
        """ユーザーがアプリ内で直接行う変更（名前変更・階層変更など）。検証と履歴保存を伴う。"""
        try:
            changed = self.store.apply(operations, reason=reason, pending_proposals=self.pending_proposal_titles())
        except TaxonomyError as e:
            return False, str(e)
        self._emit(type="taxonomy")
        return True, "変更しました" if changed else "変更はありませんでした"

    def suggest_taxonomy_changes(self) -> list[str]:
        """統合・分割の変更案を作る（提案として保存し、承認されるまで反映しない）。"""
        created = []
        tax = self.store.taxonomy
        cats = tax.active()
        pending_titles = {p.title for p in self.db.list_proposals("pending")}
        for i, a in enumerate(cats):
            for b in cats[i + 1:]:
                if a.parent_category_id != b.parent_category_id:
                    continue
                ka, kb = set(a.keywords), set(b.keywords)
                if not ka or not kb:
                    continue
                jacc = len(ka & kb) / len(ka | kb)
                if jacc >= 0.5:
                    title = f"「{b.category_name}」を「{a.category_name}」に統合"
                    if title in pending_titles:
                        continue
                    created.append(self.db.add_proposal(
                        kind="merge", title=title,
                        reason=f"分類条件の重複が大きいため（共通語: {', '.join(sorted(ka & kb)[:5])}）",
                        operations=[{"op": "merge", "source_ids": [b.category_id], "target_id": a.category_id}]))
        for c in cats:
            folder = self.organize_root.joinpath(*c.relative_path.split("/"))
            if not folder.is_dir() or tax.children(c.category_id):
                continue
            docs = []
            for p in iter_files(folder, False, self.safety):
                try:
                    d = self.analyze(p, folder)
                except OSError:
                    continue
                if d.parse.readable:
                    docs.append(d)
            if len(docs) < 6:
                continue
            draft = self.heuristic.propose_taxonomy(docs, Taxonomy())
            tops = [x for x in draft.new_categories if x.parent_category_id is None and len(x.members) >= 2]
            if len(tops) >= 2 and sum(len(x.members) for x in tops) >= 0.7 * len(docs):
                title = f"「{c.category_name}」を {len(tops)} 個の下位カテゴリに分割"
                if title in pending_titles:
                    continue
                children = []
                for x in tops:
                    spec = x.to_spec()
                    spec.pop("parent_category_id", None)
                    children.append(spec)
                created.append(self.db.add_proposal(
                    kind="split", title=title,
                    reason=f"{len(docs)}件の中に明確なまとまりが {len(tops)} 個あるため（"
                           + "、".join(x.name for x in tops) + "）",
                    operations=[{"op": "split", "category_id": c.category_id, "children": children}]))
        if created:
            self._refresh_outputs()
            self._emit(type="taxonomy")
        return created

    def consistency(self) -> list[Mismatch]:
        hist = collect_history_paths(self.store.history_snapshots())
        return check_consistency(self.store.taxonomy, self.organize_root, hist)

    def apply_consistency_fix(self, fix: dict) -> str:
        return apply_fix(fix, self.organize_root)

    # ---------- 状態 ----------
    def stats(self) -> dict:
        ops = self.db.counts()
        files = self.db.file_counts()
        return {
            "moved": ops.get("moved", 0), "failed": ops.get("failed", 0), "undone": ops.get("undone", 0),
            "review": files.get(F_REVIEW, 0), "ready": files.get(F_READY, 0),
            "queue": self.queue.pending_count(), "monitoring": self.monitor.running, "paused": self.paused,
            "taxonomy_version": self.store.taxonomy.taxonomy_version,
            "proposals": len(self.db.list_proposals("pending")),
        }

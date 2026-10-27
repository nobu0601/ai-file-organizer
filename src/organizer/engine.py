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
from classifier.heuristic import HeuristicClassifier, profile_weights, rank_terms, stem_terms
from classifier.model_select import recommended
from classifier.ollama import OllamaClassifier, OllamaClient, OllamaError
from classifier.semantic import SemanticClassifier
from classifier.validation import evaluate, looks_like_instruction
from history.vectors import VectorStore
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
        self.vectors = VectorStore(self.paths.vectors)
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
        self._update_protection()
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
                client = OllamaClient(o.base_url, o.model, o.timeout_seconds)
                namer = OllamaClassifier(client, self.heuristic)
                if o.embed_model and client.has_model(o.embed_model):
                    # 埋め込みモデルがあれば意味の近さで分類し、LLM はカテゴリ名を考える役にする
                    try:
                        client.resolve()
                    except OllamaError:
                        namer = None
                    self._llm = SemanticClassifier(client, o.embed_model, self.vectors, self.heuristic, namer)
                else:
                    self._llm = namer
            except OllamaError as e:
                log.warning("Ollama 設定が無効です: %s", e)
        if self._classifier_override is not None and hasattr(self._classifier_override, "bind_store"):
            self._classifier_override.bind_store(self.vectors)

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
        """推奨モデルと、意味の近さで分類する埋め込みモデルをダウンロードする。"""
        client = self._client()
        done = []
        for m in [self.settings.ollama.embed_model, name]:
            if m and not client.has_model(m):
                client.pull(m)
                done.append(m)
        self.rebuild()   # 自動選択をやり直す
        return f"{', '.join(done)} をダウンロードしました" if done else "必要なモデルは既にあります"

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
        if not self.settings.organize_in_place and (
                is_within(p, self.organize_root) or (recursive and is_within(self.organize_root, p))):
            raise ValueError("整理先フォルダ（またはそれを含むフォルダの再帰監視）は監視対象にできません")
        if is_within(p, self.paths.root):
            raise ValueError("アプリの管理用データ領域は監視対象にできません")
        if any(Path(w.path) == p for w in self.settings.watch_folders):
            raise ValueError("既に登録されています")
        self.settings.watch_folders.append(WatchFolder(str(p), recursive))
        self.save_settings()
        self._create_folders({c.category_id for c in self.store.taxonomy.active()}, [p])

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
        self.vectors.close()

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
        # 既存カテゴリへの一致が弱い場合（候補だけの場合を含む）は新規カテゴリも提案する
        weak = result.category_id is None or result.confidence < 0.45
        if result.new_category is not None and weak and doc.parse.readable \
                and not sensitive and not project_root:
            proposal_id = self._propose_new_category(result, str(p))
        cat = tax.get(result.category_id)
        detail = {
            "reasons": result.reasons, "decision": decision.reasons, "matched": result.matched_keywords,
            "source": result.source, "parse_status": doc.parse.status, "proposal_id": proposal_id,
            "suggested_name": cat.category_name if cat else None,
            # 提案を作った場合だけ案の名前を表示する（機密の可能性があるファイルの語は画面に出さない）
            "new_category": result.new_category.name if result.new_category and proposal_id else None,
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
        root = self.dest_root_for(p)
        dest_dir = root.joinpath(*cat.relative_path.split("/"))
        mv = self.mover.move(p, dest_dir, root, expected=snap, category_id=cat.category_id,
                             category_name=cat.category_name, reason=reason)
        if mv.ok:
            self.db.delete_file(p)
            clf = self.classifier
            if hasattr(clf, "forget"):
                clf.forget(str(p))     # 移動元のパスで覚えていた分は移動先のパスに置き換える
            self._learn(cat.category_id, [mv.dest], "moved")
            dsnap = FileSnapshot.of(Path(mv.dest))
            self.db.upsert_file(mv.dest, status=F_MOVED, size=dsnap.size, mtime=dsnap.mtime,
                                category_id=cat.category_id, reason=reason, origin="moved")
            self._emit(type="moved", src=str(p), dest=mv.dest)
        return mv

    def _learn(self, category_id: str, paths: list[str], source: str) -> None:
        """承認・移動されたファイルをカテゴリの特徴として覚える（意味の近さで分類する場合）。"""
        clf = self.classifier
        if not hasattr(clf, "learn"):
            return
        docs = []
        for p in paths:
            try:
                if Path(p).is_file():
                    docs.append(self.analyze(Path(p), None))
            except OSError:
                continue
        try:
            clf.learn(category_id, docs, source)
        except Exception:
            log.exception("学習に失敗しました")

    def _on_operations(self, operations: list[dict]) -> None:
        clf = self.classifier
        if hasattr(clf, "on_operations"):
            clf.on_operations(operations)

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
            clf = self.classifier
            if hasattr(clf, "forget"):
                clf.forget(mv.dest)
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
        self._update_protection()
        self.store.export_markdown(self.pending_proposal_titles())

    # ---------- 整理先 ----------
    def dest_roots(self) -> list[Path]:
        """フォルダを作る場所の一覧（整理するフォルダの中に作る設定なら、整理するフォルダそのもの）。"""
        if self.settings.organize_in_place and self.settings.watch_folders:
            return [Path(w.path) for w in self.settings.watch_folders]
        return [self.organize_root]

    def dest_root_for(self, path: str | Path) -> Path:
        """ファイルの移動先フォルダを作る場所。整理するフォルダの中に作る設定なら、そのファイルがあるフォルダ。"""
        if self.settings.organize_in_place and self.settings.watch_folders:
            w = self._watch_root_for(str(path))
            if w:
                return Path(w.path)
            # 整理済みのフォルダの中にあるファイル（分けるときなど）は、それを含む整理するフォルダ
            inside = [Path(w.path) for w in self.settings.watch_folders if is_within(path, w.path)]
            if inside:
                return max(inside, key=lambda r: len(str(r)))
        return self.organize_root

    def _update_protection(self) -> None:
        """整理済みのフォルダを、もう一度整理する対象から外す。"""
        prot = [self.paths.root]
        if self.settings.organize_in_place and self.settings.watch_folders:
            tops = {c.relative_path.split("/")[0] for c in self.store.taxonomy.categories.values() if c.relative_path}
            prot += [Path(w.path) / t for w in self.settings.watch_folders for t in sorted(tops)]
        else:
            prot.append(self.organize_root)
        self.safety.protected_roots[:] = prot

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
        pid = self.db.add_proposal(kind="add", title=f"新しいフォルダ「{sug.name}」",
                                   reason="今のフォルダ分けに合わないファイルが届いたため",
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
        same_name: dict[tuple, str] = {}   # (親, 名前) → 先に作った提案（同じ名前の提案を 2 つ作らない）
        alias: dict[str, str] = {}
        for c in draft.new_categories:
            parent = next((x for x in draft.new_categories if x.category_id == c.parent_category_id), None)
            spec = c.to_spec()
            spec["parent_category_id"] = alias.get(spec["parent_category_id"], spec["parent_category_id"])
            key = (spec["parent_category_id"], c.name.strip().lower())
            if key in same_name:
                prev = self.db.get_proposal(same_name[key])
                prev_spec = prev.operations[0]["category"]
                alias[c.category_id] = prev_spec["category_id"]
                rules = prev_spec.setdefault("classification_rules", {})
                rules["keywords"] = list(dict.fromkeys(rules.get("keywords", []) + c.keywords))[:30]
                files = list(dict.fromkeys(prev.related_files + c.members))
                self.db.update_proposal(prev.id, operations=prev.operations, related_files=files,
                                        reason=f"{len(files)}個のファイルに共通する言葉: {', '.join(rules['keywords'][:4])}")
                continue
            title = f"新しいフォルダ「{c.name}」" + (f"（「{parent.name}」の中）" if parent else "")
            reason = f"{len(c.members)}個のファイルに共通する言葉: {', '.join(c.keywords[:4])}"
            pid = self.db.add_proposal(kind="add", title=title, reason=reason,
                                       operations=[{"op": "add", "category": spec}],
                                       related_files=c.members, source="ai-initial")
            same_name[key] = pid
            created.append(pid)
        self._refresh_outputs()
        self._emit(type="taxonomy")
        return {"proposals": created, "analyzed": len(docs), "reused": len(draft.reused),
                "unassigned": len(draft.unassigned), "notes": draft.notes, "source": draft.source}

    def approve_proposals(self, proposal_ids: list[str]) -> tuple[bool, str]:
        """提案を 1 件ずつ採用する。同じ名前のフォルダはまとめ、採用できない提案があってもほかは進める。"""
        props = [p for p in (self.db.get_proposal(i) for i in proposal_ids) if p and p.status == "pending"]
        if not props:
            return False, "採用できる提案がありません"
        # 中のフォルダの提案は、親フォルダの提案のあとに処理する
        added = {op["category"]["category_id"] for p in props for op in p.operations if op.get("op") == "add"}
        props.sort(key=lambda p: any(op.get("op") == "add" and op["category"].get("parent_category_id") in added
                                     for op in p.operations))
        alias: dict[str, str] = {}   # 提案のフォルダ ID → 実際に使うフォルダ ID（同名フォルダにまとめた場合）
        approved, merged, failed = [], [], []
        for p in props:
            trial = dict(alias)
            ops, names = self._merge_same_name(p.operations, trial)
            try:
                if ops:
                    self.store.apply(ops, reason=f"{p.title}: {p.reason}")
            except TaxonomyError as e:
                failed.append(f"{p.title}（{e}）")
                continue
            alias = trial
            merged += names
            approved.append(p)
            self.db.set_proposal_status(p.id, "approved")
            for op in p.operations:
                if op.get("op") == "add":
                    cid = op["category"]["category_id"]
                    self._learn(alias.get(cid, cid), p.related_files, "approved")
                elif op.get("op") == "split":
                    for child in op.get("children", []):
                        if child.get("category_id"):
                            self._learn(child["category_id"], child.get("members", []), "approved")
            self._on_operations(ops)
        self._refresh_outputs()
        # 採用したフォルダは、ファイルを移動する前でもすぐ作る（仕上がりを先に確かめられるように）
        member_of: dict[str, str] = {}   # ファイル → 採用したフォルダ（提案に含まれていたファイル）
        for p in approved:
            for op in p.operations:
                if op.get("op") == "add":
                    cid = op["category"]["category_id"]
                    for f in p.related_files:
                        member_of.setdefault(f, alias.get(cid, cid))
        roots = list(dict.fromkeys(self.dest_root_for(f) for f in member_of)) or self.dest_roots()
        self._create_folders({alias.get(c, c) for c in member_of.values()} |
                             {alias.get(op["category"]["category_id"], op["category"]["category_id"])
                              for p in approved for op in p.operations if op.get("op") == "add"}, roots)
        # 関連ファイルを再分類する（採用したフォルダ分けで改めて判定）
        related = {f for p in approved for f in p.related_files}
        for f in related:
            rec = self.db.get_file(f)
            if Path(f).is_file() and (rec is None or rec.status in (F_REVIEW, F_PENDING, F_FAILED)):
                self.db.delete_file(f)
                out = self.process_file(f, rec.origin if rec and rec.origin else "scan")
                if out.status == F_REVIEW and f in member_of:
                    self._mark_member_ready(Path(f), member_of[f])
        self._emit(type="taxonomy")
        lines = []
        if approved:
            lines.append(f"{len(approved)} 件の提案を採用し、次の場所にフォルダを作りました: "
                         + "、".join(str(r) for r in roots))
            ready = sum(1 for r in self.db.list_files((F_READY,)) if r.path in related)
            if ready:
                lines.append(f"移動待ちのファイルが {ready} 件あります。「確認して移動」の画面で移動してください。")
        for name in dict.fromkeys(merged):
            lines.append(f"「{name}」は同じ名前のフォルダがあるため、1 つにまとめました。")
        if failed:
            lines.append("採用できなかった提案:")
            lines += [f"・{x}" for x in failed]
        return bool(approved), "\n".join(lines)

    def _create_folders(self, category_ids: set[str], roots: list[Path]) -> None:
        tax = self.store.taxonomy
        self._update_protection()
        for root in roots:
            for cid in category_ids:
                c = tax.get(cid)
                if c is None or not c.active:
                    continue
                try:
                    root.joinpath(*c.relative_path.split("/")).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    log.warning("フォルダを作れませんでした: %s (%s)", c.relative_path, e)

    def _mark_member_ready(self, p: Path, category_id: str) -> None:
        """採用した提案に含まれていたファイルは、あなたが見て決めた移動先として「移動待ち」にする。
        ただし、大事な情報が含まれていそうなファイルや読めないファイルなどは確認待ちのままにする。"""
        cat = self.store.taxonomy.get(category_id)
        rec = self.db.get_file(p)
        if cat is None or not cat.active or rec is None:
            return
        w = self._watch_root_for(str(p))
        root = Path(w.path) if w else None
        doc = self.analyze(p, root)
        if (not doc.parse.readable or self.safety.sensitive_reason(p, doc.parse.text)
                or self.safety.find_project_root(p, root) or looks_like_instruction(doc.parse.text)):
            return
        detail = dict(rec.detail or {}) | {"waiting": "採用した提案に含まれていたファイル",
                                           "suggested_name": cat.category_name}
        self.db.upsert_file(str(p), status=F_READY, size=rec.size, mtime=rec.mtime, category_id=cat.category_id,
                            confidence=rec.confidence, reason=f"採用したフォルダ「{cat.category_name}」の提案に含まれていたファイル",
                            detail=detail, origin=rec.origin)

    def _merge_same_name(self, operations: list[dict], alias: dict[str, str]) -> tuple[list[dict], list[str]]:
        """フォルダ追加のうち、同じ場所に同じ名前のフォルダが既にあるものは、そのフォルダへの言葉の追加に置き換える。"""
        out, names = [], []
        for op in operations:
            if op.get("op") != "add":
                out.append(op)
                continue
            spec = dict(op["category"])
            parent = spec.get("parent_category_id")
            spec["parent_category_id"] = alias.get(parent, parent)
            name = str(spec.get("category_name", "")).strip().lower()
            same = next((c for c in self.store.taxonomy.active()
                         if c.parent_category_id == spec["parent_category_id"]
                         and c.category_name.strip().lower() == name), None)
            if same is None:
                out.append({**op, "category": spec})
                continue
            alias[spec["category_id"]] = same.category_id
            new_kw = spec.get("classification_rules", {}).get("keywords", [])
            keywords = list(dict.fromkeys(same.keywords + list(new_kw)))[:30]
            if keywords != same.keywords:
                out.append({"op": "update", "category_id": same.category_id, "keywords": keywords})
            names.append(same.category_name)
        return out, names

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
        self._on_operations(operations)
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
            folders = [r.joinpath(*c.relative_path.split("/")) for r in self.dest_roots()]
            folders = [f for f in folders if f.is_dir()]
            if not folders or tax.children(c.category_id):
                continue
            docs = []
            for p in (x for folder in folders for x in iter_files(folder, False, self.safety)):
                try:
                    d = self.analyze(p, p.parent)
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
                    spec["members"] = list(x.members)   # 承認時に意味の特徴を覚えるため
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

    # ---------- 混ざったフォルダを分ける ----------
    NOT_INCLUDED = "（このフォルダに入れない）"

    def _docs_for(self, paths: list[str]) -> list[DocInput]:
        docs = []
        for f in dict.fromkeys(paths):
            p = Path(f)
            if not p.is_file():
                continue
            w = self._watch_root_for(f)
            try:
                docs.append(self.analyze(p, Path(w.path) if w else p.parent))
            except OSError:
                continue
        return docs

    def _split_plan(self, paths: list[str]) -> list[dict]:
        docs = self._docs_for(paths)
        if len(docs) < 2:
            raise ValueError("分けるには、ファイルが 2 つ以上必要です")
        clf = self.classifier
        if hasattr(clf, "split_groups"):
            draft = clf.split_groups(docs)
        else:
            draft = self.heuristic.split_groups(docs)
            if hasattr(clf, "name_draft") and len(draft.new_categories) > 1:
                draft = clf.name_draft(draft, docs)
        return [{"name": c.name, "files": list(c.members)} for c in draft.new_categories]

    def category_files(self, category_id: str) -> list[str]:
        """フォルダに入っているファイル（移動済み）と、そのフォルダへの移動待ちのファイル。"""
        c = self.store.taxonomy.get(category_id)
        if c is None:
            return []
        out = []
        for root in self.dest_roots():
            folder = root.joinpath(*c.relative_path.split("/"))
            out += [str(x) for x in iter_files(folder, False, None)] if folder.is_dir() else []
        out += [r.path for r in self.db.list_files((F_READY,)) if r.category_id == category_id]
        return list(dict.fromkeys(out))

    def plan_split_proposal(self, proposal_id: str) -> list[dict]:
        p = self.db.get_proposal(proposal_id)
        if p is None or p.status != "pending" or p.kind != "add":
            raise ValueError("分けられるのは、まだ採用していない「新しいフォルダ」の提案です")
        return self._split_plan(p.related_files)

    def plan_split_category(self, category_id: str) -> list[dict]:
        c = self.store.taxonomy.get(category_id)
        if c is None or not c.active:
            raise ValueError("フォルダが見つかりません")
        return self._split_plan(self.category_files(category_id))

    def _group_spec(self, name: str, files: list[str], universe: list[DocInput], parent_id: str | None,
                    category_id: str | None = None) -> dict:
        members = [d for d in universe if str(d.path) in set(files)]
        terms = (rank_terms(members, universe) if members else []) or stem_terms(name)
        top = terms[:8]
        return {"category_id": category_id or new_category_id(), "category_name": name.strip()[:80],
                "parent_category_id": parent_id,
                "description": f"「{name.strip()}」のファイルをまとめるフォルダ（分けて作成）",
                "classification_rules": {"keywords": top,
                                         "keyword_weights": profile_weights(members, universe, top) if members else {}},
                "exclusion_rules": [], "examples": [Path(f).name for f in files[:5]]}

    @staticmethod
    def _check_groups(groups: list[dict]) -> list[dict]:
        groups = [{"name": str(g.get("name", "")).strip(), "files": list(g.get("files", []))} for g in groups]
        groups = [g for g in groups if g["files"]]
        for g in groups:
            if not g["name"]:
                raise ValueError("名前が空のまとまりがあります")
        names = [g["name"].lower() for g in groups if g["name"] != Engine.NOT_INCLUDED]
        if len(names) != len(set(names)):
            raise ValueError("同じ名前のまとまりが 2 つあります。名前を変えるか、1 つにまとめてください")
        return groups

    def apply_split_proposal(self, proposal_id: str, groups: list[dict]) -> tuple[bool, str]:
        """まだ採用していない提案を、いくつかの提案に分け直す（採用はまだしない）。"""
        p = self.db.get_proposal(proposal_id)
        if p is None or p.status != "pending" or p.kind != "add":
            return False, "分けられる提案がありません"
        try:
            groups = self._check_groups(groups)
        except ValueError as e:
            return False, str(e)
        keep = [g for g in groups if g["name"] != self.NOT_INCLUDED]
        if not keep:
            return False, "どのまとまりにもファイルがありません"
        old = p.operations[0]["category"]
        universe = self._docs_for([f for g in keep for f in g["files"]])
        for i, g in enumerate(keep):
            # 1 つ目は元の提案のフォルダ ID を引き継ぐ（この提案の中に作る予定の提案が迷子にならないように）
            spec = self._group_spec(g["name"], g["files"], universe, old.get("parent_category_id"),
                                    old["category_id"] if i == 0 else None)
            self.db.add_proposal(kind="add", title=f"新しいフォルダ「{spec['category_name']}」",
                                 reason=f"「{old.get('category_name', '')}」を分けました（{len(g['files'])}個のファイル）",
                                 operations=[{"op": "add", "category": spec}], related_files=g["files"],
                                 source=p.source)
        self.db.set_proposal_status(p.id, "superseded")
        self._refresh_outputs()
        self._emit(type="taxonomy")
        return True, f"「{old.get('category_name', '')}」の提案を {len(keep)} 個に分けました。内容を見て採用してください。"

    def apply_split_category(self, category_id: str, groups: list[dict]) -> tuple[bool, str]:
        """採用済みのフォルダを分ける。元の名前のまとまりは元のフォルダに残し、ほかは新しいフォルダへ移動する。
        移動は履歴に残り、あとから元に戻せる。"""
        orig = self.store.taxonomy.get(category_id)
        if orig is None or not orig.active:
            return False, "フォルダが見つかりません"
        try:
            groups = [g for g in self._check_groups(groups) if g["name"] != self.NOT_INCLUDED]
        except ValueError as e:
            return False, str(e)
        if len(groups) < 2 and not (groups and groups[0]["name"].lower() != orig.category_name.lower()):
            return False, "2 つ以上のまとまりに分けてください"
        universe = self._docs_for([f for g in groups for f in g["files"]])
        siblings = {c.category_name.lower(): c for c in self.store.taxonomy.active()
                    if c.parent_category_id == orig.parent_category_id}
        ops, targets = [], []
        for g in groups:
            key = g["name"].lower()
            if key in siblings:
                target = siblings[key].category_id
                spec = self._group_spec(g["name"], g["files"], universe, orig.parent_category_id, target)
                ops.append({"op": "update", "category_id": target,
                            "keywords": spec["classification_rules"]["keywords"]})
            else:
                spec = self._group_spec(g["name"], g["files"], universe, orig.parent_category_id)
                ops.append({"op": "add", "category": spec})
                target = spec["category_id"]
            targets.append((target, g["files"]))
        kept = any(t == category_id for t, _ in targets)
        try:
            self.store.apply(ops, reason=f"ユーザーが「{orig.category_name}」を {len(groups)} 個に分けました",
                             pending_proposals=self.pending_proposal_titles())
        except TaxonomyError as e:
            return False, f"分けられません: {e}"
        self._on_operations(ops)
        self._create_folders({t for t, _ in targets}, self.dest_roots())
        moved = failed = 0
        tax = self.store.taxonomy
        for target, files in targets:
            cat = tax.get(target)
            dests = []
            for f in files:
                rec = self.db.get_file(f)
                if rec is not None and rec.status == F_READY:
                    # まだ移動していないファイルは、移動先を付け替えるだけ
                    self.db.upsert_file(f, status=F_READY, size=rec.size, mtime=rec.mtime, category_id=target,
                                        confidence=rec.confidence, reason=f"「{cat.category_name}」に分けました",
                                        detail=rec.detail, origin=rec.origin)
                    continue
                if target == category_id:
                    continue
                mv = self._move_to_category(Path(f), cat, None, f"「{orig.category_name}」から分けました")
                if mv.ok:
                    moved += 1
                    dests.append(mv.dest)
                else:
                    failed += 1
            self._learn(target, dests, "approved")
        if not kept and not tax.children(category_id):
            # 元のフォルダが空になったら、フォルダ分けから外し、空のフォルダも片付ける
            try:
                self.store.apply([{"op": "archive", "category_id": category_id}],
                                 reason=f"「{orig.category_name}」を分けたため空になりました")
                self._on_operations([{"op": "archive", "category_id": category_id}])
            except TaxonomyError:
                pass
            for root in self.dest_roots():
                try:
                    root.joinpath(*orig.relative_path.split("/")).rmdir()   # 空のときだけ消える
                except OSError:
                    pass
        self._refresh_outputs()
        self._emit(type="taxonomy")
        msg = f"「{orig.category_name}」を {len(groups)} 個に分け、{moved} 個のファイルを移動しました。"
        if failed:
            msg += f"\n{failed} 個は移動できませんでした（「履歴・元に戻す」に理由があります）。"
        msg += "\n移動は「履歴・元に戻す」からいつでも元に戻せます。"
        return True, msg

    def consistency(self) -> list[Mismatch]:
        hist = collect_history_paths(self.store.history_snapshots())
        in_place = self.settings.organize_in_place and self.settings.watch_folders
        out = []
        for root in self.dest_roots():
            for m in check_consistency(self.store.taxonomy, root, hist):
                # 整理するフォルダの中には元々あなたのフォルダがあるので、「未登録フォルダ」とは言わない
                if in_place and m.kind == "unknown_folder":
                    continue
                if len(self.dest_roots()) > 1:
                    m.path = str(root / m.path)
                if m.fix:
                    m.fix = dict(m.fix, root=str(root))
                out.append(m)
        return out

    def apply_consistency_fix(self, fix: dict) -> str:
        fix = dict(fix)
        root = Path(fix.pop("root", self.organize_root))
        return apply_fix(fix, root)

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

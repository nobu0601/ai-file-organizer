"""メイン画面: 監視対象・監視操作・処理キュー・件数。"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui.common import UiBridge, fill_tree, make_tree, short
from organizer.engine import Engine


class MainTab(ttk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change) -> None:
        super().__init__(master, padding=8)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change

        # --- 監視対象フォルダ ---
        lf = ttk.LabelFrame(self, text="監視対象フォルダ", padding=6)
        lf.pack(fill="x")
        self.folders = make_tree(lf, [("path", "フォルダ", 520), ("recursive", "サブフォルダ", 90)], height=4)
        bar = ttk.Frame(lf)
        bar.pack(fill="x", pady=(4, 0))
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Button(bar, text="フォルダを追加…", command=self.add_folder).pack(side="left")
        ttk.Checkbutton(bar, text="サブフォルダも監視する（明示的に許可）", variable=self.recursive_var).pack(side="left", padx=6)
        ttk.Button(bar, text="選択を削除", command=self.remove_folder).pack(side="left", padx=6)

        # --- 操作 ---
        ctl = ttk.LabelFrame(self, text="操作", padding=6)
        ctl.pack(fill="x", pady=6)
        row1 = ttk.Frame(ctl)
        row1.pack(fill="x")
        self.btn_start = ttk.Button(row1, text="▶ 監視開始", command=self.start)
        self.btn_stop = ttk.Button(row1, text="■ 監視停止", command=self.stop)
        self.btn_pause = ttk.Button(row1, text="⏸ 一時停止", command=self.pause)
        self.btn_resume = ttk.Button(row1, text="⏵ 再開", command=self.resume)
        for b in (self.btn_start, self.btn_stop, self.btn_pause, self.btn_resume):
            b.pack(side="left", padx=2)
        ttk.Button(row1, text="今すぐスキャン", command=self.scan).pack(side="left", padx=(12, 2))
        ttk.Button(row1, text="AI で分類体系を考案…", command=self.propose).pack(side="left", padx=2)

        row2 = ttk.Frame(ctl)
        row2.pack(fill="x", pady=(6, 0))
        s = engine.settings
        self.var_preview = tk.BooleanVar(value=s.preview_mode)
        self.var_classify = tk.BooleanVar(value=s.auto_classify)
        self.var_move = tk.BooleanVar(value=s.auto_move)
        ttk.Checkbutton(row2, text="安全なプレビューモード（移動しない）", variable=self.var_preview,
                        command=self.toggle).pack(side="left")
        ttk.Checkbutton(row2, text="自動分類", variable=self.var_classify, command=self.toggle).pack(side="left", padx=12)
        ttk.Checkbutton(row2, text="自動移動（承認済みカテゴリに明確に一致する新規ファイルのみ）",
                        variable=self.var_move, command=self.toggle).pack(side="left")

        # --- 状況 ---
        st = ttk.LabelFrame(self, text="処理状況", padding=6)
        st.pack(fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(st, textvariable=self.status_var, font=("", 10, "bold")).pack(anchor="w")
        self.counts_var = tk.StringVar()
        ttk.Label(st, textvariable=self.counts_var).pack(anchor="w")

        # --- 処理キュー ---
        qf = ttk.LabelFrame(self, text="処理キュー", padding=6)
        qf.pack(fill="both", expand=True, pady=(6, 0))
        self.queue_tree = make_tree(qf, [("status", "状態", 80), ("path", "ファイル", 420), ("origin", "検出", 70),
                                         ("message", "メッセージ", 360)], height=10)
        qb = ttk.Frame(qf)
        qb.pack(fill="x", pady=(4, 0))
        ttk.Button(qb, text="完了した項目を消去", command=self.clear_finished).pack(side="left")
        self.refresh()

    # ---------- 監視対象 ----------
    def add_folder(self) -> None:
        path = filedialog.askdirectory(title="監視するフォルダを選択")
        if not path:
            return
        rec = self.recursive_var.get()
        if rec and not messagebox.askyesno("確認", "サブフォルダも監視します。よろしいですか？\n"
                                                  "（.git や node_modules などは自動的に除外されます）"):
            return
        try:
            self.engine.add_watch_folder(path, rec)
        except ValueError as e:
            messagebox.showerror("追加できません", str(e))
        self.on_change()

    def remove_folder(self) -> None:
        for iid in self.folders.selection():
            self.engine.remove_watch_folder(iid)
        self.on_change()

    # ---------- 操作 ----------
    def start(self) -> None:
        if not self.engine.settings.watch_folders:
            messagebox.showinfo("監視", "先に監視対象フォルダを追加してください")
            return
        errors = self.engine.start_monitoring()
        # 停止中に追加されたファイルを検出する
        self.ui.run(lambda: self.engine.scan_now("startup"), lambda n: self.on_change())
        if errors:
            messagebox.showwarning("監視", "\n".join(errors))
        self.on_change()

    def stop(self) -> None:
        self.engine.stop_monitoring()
        self.on_change()

    def pause(self) -> None:
        self.engine.pause()
        self.on_change()

    def resume(self) -> None:
        self.engine.resume()
        self.on_change()

    def scan(self) -> None:
        if not self.engine.settings.watch_folders:
            messagebox.showinfo("スキャン", "先に監視対象フォルダを追加してください")
            return
        self.ui.run(lambda: self.engine.scan_now("scan"),
                    lambda n: (self.on_change(), self.status_var.set(f"スキャン完了: {n} 件をキューに追加")))

    def propose(self) -> None:
        if not self.engine.settings.watch_folders:
            messagebox.showinfo("分類体系", "先に監視対象フォルダを追加してください")
            return
        if not messagebox.askyesno("分類体系の考案",
                                   "監視対象のファイル名と内容を分析し、分類体系の案を作ります。\n"
                                   "案は承認するまで正式な体系にもフォルダにも反映されません。実行しますか？"):
            return
        self.status_var.set("分析中…（ファイル数によっては時間がかかります）")

        def done(summary):
            self.on_change()
            msg = (f"分析したファイル: {summary['analyzed']} 件\n"
                   f"新しいカテゴリ案: {len(summary['proposals'])} 件\n"
                   f"既存カテゴリに該当: {summary['reused']} 件\n"
                   f"どのカテゴリにも入らない: {summary['unassigned']} 件\n"
                   f"方式: {'ローカル LLM' if summary['source'] == 'ollama' else '内容ベース（LLM なし）'}\n\n"
                   + "\n".join(summary["notes"])
                   + "\n「分類体系」タブで案を確認・承認してください。")
            messagebox.showinfo("分類体系の考案", msg)

        self.ui.run(self.engine.propose_taxonomy, done,
                    lambda e: messagebox.showerror("分類体系の考案", f"失敗しました: {e}"))

    def toggle(self) -> None:
        s = self.engine.settings
        if self.var_move.get() and not s.auto_move:
            if not messagebox.askyesno("自動移動", "承認済みカテゴリに明確に一致する新規ファイルを自動で移動します。\n"
                                                 "（要確認・機密の可能性・低信頼度のファイルは移動しません）\n有効にしますか？"):
                self.var_move.set(False)
        s.preview_mode = self.var_preview.get()
        s.auto_classify = self.var_classify.get()
        s.auto_move = self.var_move.get()
        self.engine.save_settings()
        self.on_change()

    def clear_finished(self) -> None:
        self.engine.queue.clear_finished()
        self.refresh()

    # ---------- 表示更新 ----------
    def refresh(self) -> None:
        fill_tree(self.folders, [(w.path, (w.path, "する" if w.recursive else "しない"))
                                 for w in self.engine.settings.watch_folders])
        st = self.engine.stats()
        if st["monitoring"]:
            state = "監視中（一時停止）" if st["paused"] else "監視中"
        else:
            state = "停止中"
        mode = "プレビューモード" if self.engine.settings.preview_mode else (
            "自動移動 ON" if self.engine.settings.auto_move else "自動移動 OFF")
        busy = "  ⏳ バックグラウンド処理中" if self.ui.busy else ""
        self.status_var.set(f"状態: {state} ／ {mode} ／ 分類体系 v{st['taxonomy_version']}{busy}")
        self.counts_var.set(
            f"移動成功 {st['moved']} 件 ／ 失敗 {st['failed']} 件 ／ 取り消し {st['undone']} 件 ／ "
            f"要確認 {st['review']} 件 ／ 実行待ち {st['ready']} 件 ／ キュー {st['queue']} 件 ／ "
            f"未承認の提案 {st['proposals']} 件")
        self.btn_start.state(["disabled"] if st["monitoring"] else ["!disabled"])
        self.btn_stop.state(["!disabled"] if st["monitoring"] else ["disabled"])
        self.btn_pause.state(["!disabled"] if st["monitoring"] and not st["paused"] else ["disabled"])
        self.btn_resume.state(["!disabled"] if st["paused"] else ["disabled"])
        origin = {"event": "監視", "scan": "スキャン", "startup": "起動時"}
        fill_tree(self.queue_tree, [(i.path, (i.status, short(i.path), origin.get(i.origin, i.origin), i.message))
                                    for i in self.engine.queue.snapshot()])

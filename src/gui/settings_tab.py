"""設定画面。"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gui.common import UiBridge, open_path
from safety.rules import is_within
from organizer.engine import Engine


def _startup_script_path() -> Path | None:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AIFileOrganizer.cmd"


def set_autostart(enabled: bool) -> str:
    """Windows のスタートアップフォルダに起動用 .cmd を置く/消す（このアプリが作ったものだけ）。"""
    p = _startup_script_path()
    if p is None:
        return "自動起動は Windows でのみ設定できます"
    main = Path(__file__).resolve().parents[1] / "main.py"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    marker = "rem created-by-AIFileOrganizer"
    if enabled:
        p.write_text(f'@echo off\n{marker}\nstart "" "{exe}" "{main}" --minimized\n', encoding="utf-8")
        return f"自動起動を設定しました: {p}"
    if p.exists() and marker in p.read_text(encoding="utf-8", errors="ignore"):
        p.unlink()
    return "自動起動を解除しました"


class SettingsTab(ttk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change) -> None:
        super().__init__(master, padding=8)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change
        s = engine.settings
        canvas = ttk.Frame(self)
        canvas.pack(fill="both", expand=True)
        g = ttk.Frame(canvas)
        g.pack(fill="x")
        r = 0

        def row(label, widget, hint=""):
            nonlocal r
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="nw", padx=4, pady=3)
            widget.grid(row=r, column=1, sticky="we", padx=4, pady=3)
            if hint:
                ttk.Label(g, text=hint, foreground="#666").grid(row=r, column=2, sticky="w")
            r += 1

        g.columnconfigure(1, weight=1)
        self.v_root = tk.StringVar(value=s.organize_root)
        f = ttk.Frame(g)
        ttk.Entry(f, textvariable=self.v_root, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="参照…", command=self.pick_root).pack(side="left", padx=4)
        row("整理先フォルダ", f, "カテゴリのフォルダはこの下に作られます")
        self.v_thr = tk.DoubleVar(value=s.confidence_threshold)
        row("自動移動の信頼度閾値", ttk.Spinbox(g, from_=0.5, to=1.0, increment=0.01, textvariable=self.v_thr, width=8),
            "初期値 0.85。これ未満は要確認")
        self.v_checks = tk.IntVar(value=s.stability_checks)
        row("安定確認の回数", ttk.Spinbox(g, from_=1, to=20, textvariable=self.v_checks, width=8),
            "サイズと更新日時が連続して同じである回数")
        self.v_quiet = tk.DoubleVar(value=s.stability_quiet_seconds)
        row("最後の変更からの待機秒数", ttk.Spinbox(g, from_=0, to=600, increment=0.5, textvariable=self.v_quiet, width=8))
        self.v_poll = tk.DoubleVar(value=s.stability_poll_seconds)
        row("確認間隔（秒）", ttk.Spinbox(g, from_=0.2, to=60, increment=0.1, textvariable=self.v_poll, width=8))
        self.v_maxmb = tk.DoubleVar(value=round(s.max_parse_bytes / 1024 / 1024, 1))
        row("解析する最大サイズ（MB）", ttk.Spinbox(g, from_=0.1, to=200, increment=0.5, textvariable=self.v_maxmb, width=8),
            "これを超える部分は読み込みません")
        self.v_sha = tk.BooleanVar(value=s.use_sha256)
        row("SHA-256 で内容を記録", ttk.Checkbutton(g, variable=self.v_sha), "取り消し時の改変検出に使用")

        self.t_dirs = self._text(g, s.excluded_dirs)
        row("移動禁止フォルダ名", self.t_dirs, "1 行に 1 つ")
        self.t_exts = self._text(g, s.excluded_extensions)
        row("移動禁止の拡張子", self.t_exts)
        self.t_names = self._text(g, s.excluded_names)
        row("移動禁止のファイル名パターン", self.t_names, "* ? のワイルドカード可")
        self.t_paths = self._text(g, s.excluded_paths, height=3)
        row("除外するフォルダ（フルパス）", self.t_paths, "重要ファイルの置き場所など")

        lf = ttk.LabelFrame(canvas, text="ローカル AI（Ollama）", padding=6)
        lf.pack(fill="x", pady=6)
        self.v_ollama = tk.BooleanVar(value=s.ollama.enabled)
        ttk.Checkbutton(lf, text="Ollama を使う（使えない場合は自動的に内容ベース分類になります）",
                        variable=self.v_ollama).grid(row=0, column=0, columnspan=3, sticky="w")
        self.v_url = tk.StringVar(value=s.ollama.base_url)
        self.v_model = tk.StringVar(value=s.ollama.model)
        ttk.Label(lf, text="URL（ローカルのみ）").grid(row=1, column=0, sticky="w")
        ttk.Entry(lf, textvariable=self.v_url, width=40).grid(row=1, column=1, sticky="w")
        ttk.Label(lf, text="モデル").grid(row=2, column=0, sticky="w")
        ttk.Entry(lf, textvariable=self.v_model, width=40).grid(row=2, column=1, sticky="w")
        ttk.Button(lf, text="接続テスト", command=self.test_ollama).grid(row=1, column=2, rowspan=2, padx=6)

        other = ttk.LabelFrame(canvas, text="その他", padding=6)
        other.pack(fill="x")
        self.v_auto = tk.BooleanVar(value=s.autostart)
        ttk.Checkbutton(other, text="Windows 起動時にアプリを起動する（初期設定: 無効）", variable=self.v_auto).pack(anchor="w")
        bar = ttk.Frame(other)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="データ保存先を開く", command=lambda: open_path(engine.paths.root)).pack(side="left")
        ttk.Button(bar, text="ログを開く", command=lambda: open_path(engine.paths.log_dir)).pack(side="left", padx=4)
        ttk.Button(self, text="設定を保存", command=self.save).pack(anchor="e", pady=6)

    @staticmethod
    def _text(master, items, height=4) -> tk.Text:
        t = tk.Text(master, height=height, width=60)
        t.insert("1.0", "\n".join(items))
        return t

    @staticmethod
    def _lines(t: tk.Text) -> list[str]:
        return [x.strip() for x in t.get("1.0", "end").splitlines() if x.strip()]

    def pick_root(self) -> None:
        p = filedialog.askdirectory(title="整理先フォルダを選択")
        if p:
            self.v_root.set(p)

    def test_ollama(self) -> None:
        self._apply_ollama()
        self.ui.run(self.engine.test_ollama,
                    lambda res: (messagebox.showinfo if res[0] else messagebox.showwarning)("Ollama", res[1]))

    def _apply_ollama(self) -> None:
        o = self.engine.settings.ollama
        o.enabled = self.v_ollama.get()
        o.base_url = self.v_url.get().strip()
        o.model = self.v_model.get().strip()

    def save(self) -> None:
        s = self.engine.settings
        try:
            new_root = Path(self.v_root.get()).expanduser()
            for w in s.watch_folders:
                if w.recursive and is_within(new_root, w.path):
                    if not messagebox.askyesno("確認", "整理先が再帰監視フォルダの中にあります。整理先は監視から除外されますが、"
                                                     "よろしいですか？"):
                        return
            s.organize_root = str(new_root)
            s.confidence_threshold = min(1.0, max(0.0, float(self.v_thr.get())))
            s.stability_checks = max(1, int(self.v_checks.get()))
            s.stability_quiet_seconds = max(0.0, float(self.v_quiet.get()))
            s.stability_poll_seconds = max(0.2, float(self.v_poll.get()))
            s.max_parse_bytes = int(float(self.v_maxmb.get()) * 1024 * 1024)
        except (ValueError, tk.TclError) as e:
            messagebox.showerror("設定", f"入力値が不正です: {e}")
            return
        s.use_sha256 = self.v_sha.get()
        s.excluded_dirs = self._lines(self.t_dirs)
        s.excluded_extensions = self._lines(self.t_exts)
        s.excluded_names = self._lines(self.t_names)
        s.excluded_paths = self._lines(self.t_paths)
        self._apply_ollama()
        msg = ""
        if self.v_auto.get() != s.autostart:
            try:
                msg = set_autostart(self.v_auto.get())
                s.autostart = self.v_auto.get() and os.name == "nt"
            except OSError as e:
                msg = f"自動起動の設定に失敗しました: {e}"
        self.engine.save_settings()
        messagebox.showinfo("設定", "保存しました。" + (f"\n{msg}" if msg else ""))
        self.on_change()

"""メインウィンドウ。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from gui.common import UiBridge
from gui.history_tab import HistoryTab
from gui.main_tab import MainTab
from gui.review_tab import ReviewTab
from gui.settings_tab import SettingsTab
from gui.taxonomy_tab import TaxonomyTab
from organizer.engine import Engine


class App:
    def __init__(self, engine: Engine, root: tk.Tk | None = None) -> None:
        self.engine = engine
        self.root = root or tk.Tk()
        self.root.title("AI File Organizer — AI 自律型ファイル整理")
        self.root.geometry("1200x780")
        self.root.minsize(900, 600)
        try:
            ttk.Style().theme_use("vista" if "vista" in ttk.Style().theme_names() else "clam")
        except tk.TclError:
            pass
        self.ui = UiBridge(self.root)
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)
        self.tabs = {
            "main": MainTab(nb, engine, self.ui, self.refresh_all),
            "review": ReviewTab(nb, engine, self.ui, self.refresh_all),
            "taxonomy": TaxonomyTab(nb, engine, self.ui, self.refresh_all),
            "history": HistoryTab(nb, engine, self.ui, self.refresh_all),
            "settings": SettingsTab(nb, engine, self.ui, self.refresh_all),
        }
        nb.add(self.tabs["main"], text="メイン")
        nb.add(self.tabs["review"], text="要確認・実行待ち")
        nb.add(self.tabs["taxonomy"], text="分類体系")
        nb.add(self.tabs["history"], text="処理履歴")
        nb.add(self.tabs["settings"], text="設定")
        self.notebook = nb
        self._dirty = False
        # エンジンの通知（別スレッド）→ メインスレッドで画面更新
        engine.add_listener(lambda ev: self.ui.call(self._on_event, ev))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(1000, self._tick)

    def _on_event(self, ev: dict) -> None:
        self._dirty = True

    def _tick(self) -> None:
        # キュー表示は常に、その他はイベントがあったときだけ更新する
        self.tabs["main"].refresh()
        if self._dirty:
            self._dirty = False
            self.refresh_all()
        self.root.after(1000, self._tick)

    def refresh_all(self) -> None:
        for t in self.tabs.values():
            if hasattr(t, "refresh"):
                t.refresh()

    def on_close(self) -> None:
        if self.ui.busy and not messagebox.askyesno("終了", "バックグラウンド処理中です。終了しますか？"):
            return
        self.engine.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

"""メインウィンドウ。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from gui.common import UiBridge
from gui.history_tab import HistoryTab
from gui.home_tab import HomeTab
from gui.main_tab import MainTab
from gui.review_tab import ReviewTab
from gui.settings_tab import SettingsTab
from gui.taxonomy_tab import TaxonomyTab
from organizer.engine import Engine


# Windows の標準フォントを優先して使う（無い環境では日本語が表示できるものを探す）
FONT_CANDIDATES = ["Yu Gothic UI", "Meiryo UI", "Meiryo", "Noto Sans CJK JP", "IPAGothic", "IPAPGothic"]


def setup_look(root: tk.Misc) -> None:
    """文字を大きめにし、余白とボタンの見た目を整える。"""
    families = set(tkfont.families(root))
    family = next((f for f in FONT_CANDIDATES if f in families), None)
    for name, size in (("TkDefaultFont", 11), ("TkTextFont", 11), ("TkMenuFont", 11),
                       ("TkHeadingFont", 11), ("TkCaptionFont", 11)):
        try:
            f = tkfont.nametofont(name)
            f.configure(size=size, **({"family": family} if family else {}))
        except tk.TclError:
            pass
    style = ttk.Style(root)
    try:
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
    except tk.TclError:
        pass
    style.configure("TButton", padding=(12, 6))
    style.configure("Accent.TButton", padding=(16, 8), font=(family or "TkDefaultFont", 12, "bold"),
                    foreground="white", background="#2563eb")
    style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#9ca3af")],
              foreground=[("disabled", "#f3f4f6")])
    style.configure("TNotebook.Tab", padding=(10, 6))
    style.configure("Treeview", rowheight=28)
    style.configure("Treeview.Heading", font=(family or "TkDefaultFont", 11, "bold"))


class App:
    def __init__(self, engine: Engine, root: tk.Tk | None = None) -> None:
        self.engine = engine
        self.root = root or tk.Tk()
        self.root.title("AI ファイル整理")
        self.root.geometry("1200x780")
        self.root.minsize(900, 600)
        setup_look(self.root)
        self.ui = UiBridge(self.root)
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)
        detail = MainTab(nb, engine, self.ui, self.refresh_all)
        self.tabs = {
            "review": ReviewTab(nb, engine, self.ui, self.refresh_all),
            "taxonomy": TaxonomyTab(nb, engine, self.ui, self.refresh_all),
            "history": HistoryTab(nb, engine, self.ui, self.refresh_all),
            "main": detail,
            "settings": SettingsTab(nb, engine, self.ui, self.refresh_all),
        }
        self.tabs = {"home": HomeTab(nb, engine, self.ui, self.refresh_all, detail, self.goto), **self.tabs}
        # よく使う画面を前に、細かい機能（詳細・設定）は後ろに置く
        nb.add(self.tabs["home"], text="  ホーム  ")
        nb.add(self.tabs["review"], text="  確認して移動  ")
        nb.add(self.tabs["taxonomy"], text="  フォルダ分け  ")
        nb.add(self.tabs["history"], text="  履歴・元に戻す  ")
        nb.add(self.tabs["main"], text="  詳細  ")
        nb.add(self.tabs["settings"], text="  設定  ")
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
        self.tabs["home"].refresh()
        if self._dirty:
            self._dirty = False
            self.refresh_all()
        self.root.after(1000, self._tick)

    def goto(self, key: str) -> None:
        self.notebook.select(self.tabs[key])
        self.refresh_all()

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

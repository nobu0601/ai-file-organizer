"""GUI 共通部品: バックグラウンド実行、ファイルを開く、表の作成。"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

log = logging.getLogger(__name__)


class UiBridge:
    """別スレッドからの通知を Tk のメインスレッドへ渡す。Tk のウィジェットはメインスレッドからのみ触る。"""

    def __init__(self, root: tk.Misc, interval_ms: int = 250) -> None:
        self.root = root
        self.interval = interval_ms
        self.q: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self.busy = 0
        self._poll()

    def call(self, fn: Callable, *args: Any) -> None:
        self.q.put((fn, args))

    def _poll(self) -> None:
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("UI callback error")
        except queue.Empty:
            pass
        self.root.after(self.interval, self._poll)

    def run(self, work: Callable[[], Any], done: Callable[[Any], None] | None = None,
            error: Callable[[Exception], None] | None = None) -> None:
        """重い処理をバックグラウンドで実行し、結果をメインスレッドで受け取る。"""
        self.busy += 1

        def target():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                log.exception("background task failed")
                self.call(self._finish, error, e)
                return
            self.call(self._finish, done, res)

        threading.Thread(target=target, daemon=True).start()

    def _finish(self, cb, value) -> None:
        self.busy = max(0, self.busy - 1)
        if cb:
            cb(value)


def open_path(path: str | Path) -> None:
    """エクスプローラー（または既定のアプリ）で開く。ファイルを実行するのではなく関連付けで開くだけ。"""
    p = str(path)
    if os.name == "nt":
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def reveal_in_explorer(path: str | Path) -> None:
    p = Path(path)
    if os.name == "nt" and p.exists() and p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        open_path(p if p.is_dir() else p.parent)


def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], height: int = 10,
              selectmode: str = "extended", show_tree: bool = False) -> ttk.Treeview:
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show=("tree headings" if show_tree else "headings"),
                        height=height, selectmode=selectmode)
    for cid, label, width in columns:
        tree.heading(cid, text=label)
        tree.column(cid, width=width, stretch=True, anchor="w")
    if show_tree:
        tree.column("#0", width=240, stretch=True)
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def fill_tree(tree: ttk.Treeview, rows: list[tuple[str, tuple]]) -> None:
    """選択状態を保ったまま行を入れ替える。rows: (iid, values)"""
    selected = set(tree.selection())
    tree.delete(*tree.get_children())
    for iid, values in rows:
        tree.insert("", "end", iid=iid, values=values)
    keep = [i for i in selected if tree.exists(i)]
    if keep:
        tree.selection_set(keep)


def short(path: str, n: int = 70) -> str:
    return path if len(path) <= n else "…" + path[-(n - 1):]

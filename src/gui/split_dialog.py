"""混ざったフォルダを分ける画面。AI の案を表示し、ファイルの行き先とまとまりの名前を直してから決める。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from gui.common import fill_tree, make_tree, short


class SplitDialog(tk.Toplevel):
    """plan: [{"name": str, "files": [path, ...]}, ...]
    on_apply(groups) -> (ok, message)。ok なら閉じる。"""

    def __init__(self, master, title: str, plan: list[dict], on_apply, allow_exclude: bool,
                 exclude_label: str) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("1040x660")
        self.minsize(760, 480)
        self.transient(master.winfo_toplevel())
        self.on_apply = on_apply
        self.exclude_label = exclude_label
        self.names = [g["name"] for g in plan]
        if allow_exclude:
            self.names.append(exclude_label)
        self.where = {f: g["name"] for g in plan for f in g["files"]}   # ファイル → まとまりの名前

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        # 決定ボタンは先に下へ置く（窓が小さくても隠れないように）
        bottom = ttk.Frame(body)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(bottom, text="この分け方で決める", style="Accent.TButton", command=self.apply).pack(side="right")
        ttk.Button(bottom, text="やめる", command=self.destroy).pack(side="right", padx=8)
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        ttk.Label(body, text=title, font=(family, 14, "bold")).pack(anchor="w")
        ttk.Label(body, text="AI が分け方を考えました。行き先が違うファイルは、選んでから下の「行き先を変える」で直してください。\n"
                             "左のまとまりを選ぶと、そのまとまりのファイルだけを表示します。名前も変えられます。",
                  justify="left").pack(anchor="w", pady=(4, 8))

        pw = ttk.PanedWindow(body, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=1)
        pw.add(right, weight=2)

        ttk.Label(left, text="まとまり（フォルダ）").pack(anchor="w")
        self.groups = make_tree(left, [("name", "名前", 170), ("count", "数", 50)], height=10,
                                selectmode="browse")
        self.groups.column("count", stretch=False, anchor="e")
        self.groups.bind("<<TreeviewSelect>>", lambda e: self._show_group())
        gb = ttk.Frame(left)
        gb.pack(fill="x", pady=(4, 0))
        ttk.Button(gb, text="名前を変える", command=self.rename_group).pack(fill="x")
        ttk.Button(gb, text="まとまりを追加", command=self.add_group).pack(fill="x", pady=(4, 0))

        self.files_label = tk.StringVar(value="ファイル")
        ttk.Label(right, textvariable=self.files_label).pack(anchor="w")
        self.files = make_tree(right, [("file", "ファイル名", 280), ("group", "行き先", 180), ("folder", "場所", 260)],
                               height=14)
        mv = ttk.Frame(right)
        mv.pack(fill="x", pady=(4, 0))
        ttk.Label(mv, text="選んだファイルの行き先:").pack(side="left")
        self.target = tk.StringVar()
        self.target_box = ttk.Combobox(mv, textvariable=self.target, state="readonly", width=28)
        self.target_box.pack(side="left", padx=4)
        ttk.Button(mv, text="行き先を変える", command=self.reassign).pack(side="left")
        ttk.Button(mv, text="すべて表示", command=self._show_all).pack(side="left", padx=(12, 0))
        self._refresh()
        self._show_all()

    # ---------- 表示 ----------
    def _refresh(self) -> None:
        counts = {n: 0 for n in self.names}
        for g in self.where.values():
            counts[g] = counts.get(g, 0) + 1
        sel = self.groups.selection()
        fill_tree(self.groups, [(n, (n, counts.get(n, 0))) for n in self.names])
        if sel and self.groups.exists(sel[0]):
            self.groups.selection_set(sel)
        self.target_box["values"] = self.names
        if self.target.get() not in self.names:
            self.target.set(self.names[0] if self.names else "")
        self._fill_files()

    def _fill_files(self) -> None:
        only = getattr(self, "_only", None)
        rows = [(f, (Path(f).name, g, short(str(Path(f).parent), 40)))
                for f, g in sorted(self.where.items(), key=lambda x: (self.names.index(x[1]), Path(x[0]).name))
                if only is None or g == only]
        fill_tree(self.files, rows)
        self.files_label.set(f"「{only}」のファイル" if only else "すべてのファイル")

    def _show_group(self) -> None:
        sel = self.groups.selection()
        self._only = sel[0] if sel else None
        if sel:
            self.target.set(sel[0])
        self._fill_files()

    def _show_all(self) -> None:
        self._only = None
        self.groups.selection_remove(self.groups.selection())
        self._fill_files()

    # ---------- 操作 ----------
    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name = simpledialog.askstring(title, "フォルダの名前", initialvalue=initial, parent=self)
        if name is None:
            return None
        name = name.strip()
        if not name:
            return None
        if name.lower() in {n.lower() for n in self.names if n != initial}:
            messagebox.showwarning(title, "同じ名前のまとまりがあります", parent=self)
            return None
        return name

    def rename_group(self) -> None:
        sel = self.groups.selection()
        if not sel:
            messagebox.showinfo("名前を変える", "左の一覧からまとまりを選んでください", parent=self)
            return
        old = sel[0]
        if old == self.exclude_label:
            return
        new = self._ask_name("名前を変える", old)
        if not new or new == old:
            return
        self.names[self.names.index(old)] = new
        self.where = {f: (new if g == old else g) for f, g in self.where.items()}
        self._only = new if getattr(self, "_only", None) == old else getattr(self, "_only", None)
        self._refresh()
        self.groups.selection_set(new)

    def add_group(self) -> None:
        name = self._ask_name("まとまりを追加")
        if not name:
            return
        pos = len(self.names) - (1 if self.exclude_label in self.names else 0)
        self.names.insert(pos, name)
        self.target.set(name)
        self._refresh()

    def reassign(self) -> None:
        files = self.files.selection()
        target = self.target.get()
        if not files or not target:
            messagebox.showinfo("行き先を変える", "右の一覧からファイルを選び、行き先を選んでください", parent=self)
            return
        for f in files:
            self.where[f] = target
        self._refresh()

    def groups_result(self) -> list[dict]:
        return [{"name": n, "files": [f for f, g in self.where.items() if g == n]} for n in self.names]

    def apply(self) -> None:
        groups = [g for g in self.groups_result() if g["files"]]
        real = [g for g in groups if g["name"] != self.exclude_label]
        if len(real) < 2 and not messagebox.askyesno(
                "確認", "まとまりが 1 つしかありません。この内容で決めますか？", parent=self):
            return
        ok, msg = self.on_apply(groups)
        if ok:
            self.destroy()
            messagebox.showinfo("分けました", msg)
        else:
            messagebox.showerror("分けられません", msg, parent=self)

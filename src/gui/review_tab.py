"""要確認・実行待ちファイルの確認画面（プレビュー）。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from gui.common import UiBridge, fill_tree, make_tree, reveal_in_explorer, short
from history.db import F_FAILED, F_PENDING, F_READY, F_REVIEW
from organizer.engine import Engine

REFERENCE_BELOW = 0.5   # これ未満の信頼度の候補は「参考」として扱う


def is_reference(r) -> bool:
    """要確認ファイルの候補が参考程度（信頼度が低い）か。"""
    return r.status != F_READY and r.confidence is not None and r.confidence < REFERENCE_BELOW


LABEL = {F_READY: "実行待ち", F_REVIEW: "要確認", F_FAILED: "失敗", F_PENDING: "未分類"}


class ReviewTab(ttk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change) -> None:
        super().__init__(master, padding=8)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change
        ttk.Label(self, text="「実行待ち」は安全条件を満たし、確認後に移動できるファイルです。"
                             "「要確認」は自動では移動しません（理由を確認し、手動でカテゴリを指定できます）。",
                  wraplength=1150).pack(anchor="w")
        flt = ttk.Frame(self)
        flt.pack(fill="x", pady=4)
        self.filter_var = tk.StringVar(value="すべて")
        ttk.Label(flt, text="表示:").pack(side="left")
        cb = ttk.Combobox(flt, textvariable=self.filter_var, values=["すべて", "実行待ち", "要確認", "失敗"],
                          state="readonly", width=10)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        # 信頼度の低い候補は「参考」として薄く表示する。不要なら非表示にできる
        self.show_ref_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(flt, text=f"参考候補も表示（信頼度 {REFERENCE_BELOW:.1f} 未満の候補は薄い文字で表示）",
                        variable=self.show_ref_var, command=self.refresh).pack(side="left", padx=12)

        self.tree = make_tree(self, [("status", "状態", 70), ("file", "ファイル名", 260), ("folder", "場所", 220),
                                     ("category", "分類先（候補）", 180), ("conf", "信頼度", 60),
                                     ("reason", "理由", 460)], height=16)
        self.tree.tag_configure("reference", foreground="#9a9a9a")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
        self.detail = tk.Text(self, height=6, wrap="word")
        self.detail.pack(fill="x", pady=4)
        self.detail.configure(state="disabled")

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="選択した実行待ちを移動", command=self.execute_selected).pack(side="left")
        ttk.Button(bar, text="実行待ちをすべて移動", command=self.execute_all).pack(side="left", padx=4)
        ttk.Label(bar, text="  カテゴリ指定:").pack(side="left")
        self.cat_var = tk.StringVar()
        self.cat_box = ttk.Combobox(bar, textvariable=self.cat_var, state="readonly", width=34)
        self.cat_box.pack(side="left", padx=4)
        ttk.Button(bar, text="指定カテゴリへ移動", command=self.move_manual).pack(side="left")
        ttk.Button(bar, text="再分析", command=self.reanalyze).pack(side="left", padx=4)
        ttk.Button(bar, text="対象外にする", command=self.dismiss).pack(side="left")
        ttk.Button(bar, text="場所を開く", command=self.reveal).pack(side="left", padx=4)
        self._cats: dict[str, str] = {}
        self.refresh()

    def _records(self):
        f = self.filter_var.get()
        want = {"実行待ち": (F_READY,), "要確認": (F_REVIEW, F_PENDING), "失敗": (F_FAILED,)}.get(
            f, (F_READY, F_REVIEW, F_PENDING, F_FAILED))
        return self.engine.db.list_files(want)

    def refresh(self) -> None:
        tax = self.engine.store.taxonomy
        rows = []
        self._by_path = {}
        show_ref = self.show_ref_var.get()
        for r in self._records():
            cat = tax.get(r.category_id)
            tags: tuple = ()
            if cat and is_reference(r):
                # 信頼度の低い候補: 参考として薄く表示（非表示の設定なら候補を出さない）
                name = f"（参考）{cat.relative_path}" if show_ref else "—"
                tags = ("reference",) if show_ref else ()
            elif cat:
                name = cat.relative_path
            else:
                name = (f"（新規案）{r.detail.get('new_category')}" if r.detail.get("new_category") else "—")
            hide_conf = cat is not None and is_reference(r) and not show_ref
            conf = f"{r.confidence:.2f}" if r.confidence is not None and not hide_conf else ""
            p = Path(r.path)
            rows.append((r.path, (LABEL.get(r.status, r.status), p.name, short(str(p.parent), 40), name, conf,
                                  r.reason or ""), tags))
            self._by_path[r.path] = r
        fill_tree(self.tree, rows)
        self._cats = {c.relative_path: c.category_id for c in tax.active()}
        self.cat_box["values"] = list(self._cats)

    def show_detail(self) -> None:
        sel = self.tree.selection()
        text = ""
        if sel:
            r = self._by_path.get(sel[0])
            if r:
                cat = self.engine.store.taxonomy.get(r.category_id)
                if cat and (self.show_ref_var.get() or not is_reference(r)):
                    self.cat_var.set(cat.relative_path)   # 候補をそのまま移動先に指定できるようにする
                d = r.detail
                text = (f"ファイル: {r.path}\n判定: {r.reason}\n"
                        f"分類の根拠: {' / '.join(d.get('reasons', []))}\n"
                        f"一致キーワード: {', '.join(d.get('matched', []))}\n"
                        f"解析状態: {d.get('parse_status', '')}  方式: {d.get('source', '')}  {d.get('waiting', '')}")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _report(self, outs) -> None:
        ok = sum(1 for o in outs if o.ok)
        ng = [o for o in outs if not o.ok]
        msg = f"移動: {ok} 件 / 失敗: {len(ng)} 件"
        if ng:
            msg += "\n" + "\n".join(f"・{short(o.src, 50)}: {o.message}" for o in ng[:10])
        messagebox.showinfo("結果", msg)
        self.on_change()

    def execute_selected(self) -> None:
        paths = [p for p in self.tree.selection() if self._by_path.get(p) and self._by_path[p].status == F_READY]
        if not paths:
            messagebox.showinfo("移動", "「実行待ち」のファイルを選択してください")
            return
        if messagebox.askyesno("確認", f"{len(paths)} 件を分類先へ移動します。よろしいですか？"):
            self.ui.run(lambda: self.engine.execute_ready(paths), self._report)

    def execute_all(self) -> None:
        n = len(self.engine.db.list_files((F_READY,)))
        if not n:
            messagebox.showinfo("移動", "実行待ちのファイルはありません")
            return
        if messagebox.askyesno("確認", f"実行待ちの {n} 件をすべて移動します。よろしいですか？\n（移動は履歴から取り消せます）"):
            self.ui.run(lambda: self.engine.execute_ready(None), self._report)

    def move_manual(self) -> None:
        sel = list(self.tree.selection())
        cid = self._cats.get(self.cat_var.get())
        if not sel or not cid:
            messagebox.showinfo("移動", "ファイルと移動先カテゴリを選択してください")
            return
        if not messagebox.askyesno("確認", f"{len(sel)} 件を「{self.cat_var.get()}」へ移動します。よろしいですか？"):
            return
        self.ui.run(lambda: [self.engine.move_manually(p, cid) for p in sel], self._report)

    def reanalyze(self) -> None:
        sel = list(self.tree.selection())
        self.ui.run(lambda: [self.engine.reanalyze(p) for p in sel], lambda _: self.on_change())

    def dismiss(self) -> None:
        for p in self.tree.selection():
            self.engine.dismiss(p)
        self.on_change()

    def reveal(self) -> None:
        sel = self.tree.selection()
        if sel:
            reveal_in_explorer(sel[0])

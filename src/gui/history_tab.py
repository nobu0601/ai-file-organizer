"""処理履歴画面と取り消し。"""
from __future__ import annotations

from tkinter import messagebox, ttk

from gui.common import UiBridge, fill_tree, make_tree, reveal_in_explorer
from organizer.engine import Engine

RESULT = {"moved": "移動済み", "failed": "失敗", "undone": "取り消し済み"}


class HistoryTab(ttk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change) -> None:
        super().__init__(master, padding=8)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change
        self.tree = make_tree(self, [("id", "ID", 50), ("time", "処理日時", 150), ("result", "結果", 80),
                                     ("src", "元のパス", 300), ("dest", "移動先", 300), ("category", "カテゴリ", 140),
                                     ("reason", "分類理由", 300), ("error", "エラー", 260)], height=20)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="選んだ移動を元に戻す", command=self.undo).pack(side="left")
        ttk.Button(bar, text="移動先を開く", command=self.reveal).pack(side="left", padx=4)
        ttk.Button(bar, text="更新", command=self.refresh).pack(side="left")
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for op in self.engine.db.list_operations(1000):
            err = op.error or (f"取り消し不可: {op.undo_error}" if op.undo_error else "")
            rows.append((str(op.id), (op.id, op.created_at, RESULT.get(op.result, op.result), op.src_path,
                                      op.dest_path or "", op.category_name or "", op.reason or "", err)))
        fill_tree(self.tree, rows)

    def undo(self) -> None:
        ids = [int(i) for i in self.tree.selection()]
        if not ids:
            messagebox.showinfo("取り消し", "取り消す履歴を選択してください")
            return
        if not messagebox.askyesno("取り消し", f"{len(ids)} 件の整理を取り消し、元の場所へ戻しますか？\n"
                                             "（元の場所に同名ファイルがある場合や、移動後に変更された場合は戻しません）"):
            return

        def done(outs):
            msg = "\n".join(f"#{o.op_id}: {o.message}" for o in outs)
            messagebox.showinfo("取り消し", msg)
            self.on_change()

        self.ui.run(lambda: [self.engine.undo(i) for i in ids], done)

    def reveal(self) -> None:
        sel = self.tree.selection()
        if sel:
            op = self.engine.db.get_operation(int(sel[0]))
            if op and op.dest_path:
                reveal_in_explorer(op.dest_path)

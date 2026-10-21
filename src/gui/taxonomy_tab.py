"""分類体系画面: ツリー・詳細・提案の承認/拒否・整合性チェック。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from gui.common import UiBridge, fill_tree, make_tree, open_path
from organizer.engine import Engine


class TaxonomyTab(ttk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change) -> None:
        super().__init__(master, padding=8)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.version_var = tk.StringVar()
        ttk.Label(top, textvariable=self.version_var, font=("", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Markdown を開く", command=lambda: open_path(engine.paths.folder_map_md)).pack(side="right")
        ttk.Button(top, text="JSON の保存先を開く", command=lambda: open_path(engine.paths.taxonomy_dir)).pack(side="right", padx=4)
        ttk.Button(top, text="整理先フォルダを開く", command=self.open_root).pack(side="right")

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=6)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=1)
        pw.add(right, weight=2)

        ttk.Label(left, text="承認済みのフォルダ体系").pack(anchor="w")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
        cb = ttk.Frame(left)
        cb.pack(fill="x", pady=4)
        ttk.Button(cb, text="名前を変更", command=self.rename).pack(side="left")
        ttk.Button(cb, text="親を変更", command=self.reparent).pack(side="left", padx=4)
        ttk.Button(cb, text="説明を編集", command=self.edit_description).pack(side="left")

        self.detail = tk.Text(right, height=12, wrap="word")
        self.detail.pack(fill="x")
        self.detail.configure(state="disabled")

        pf = ttk.LabelFrame(right, text="未承認の提案（AI の案。承認するまで正式体系に反映されません）", padding=4)
        pf.pack(fill="both", expand=True, pady=6)
        self.props = make_tree(pf, [("kind", "種類", 60), ("title", "内容", 330), ("reason", "理由", 300),
                                    ("files", "関連", 50)], height=7)
        self.props.bind("<<TreeviewSelect>>", lambda e: self.show_proposal())
        pb = ttk.Frame(pf)
        pb.pack(fill="x", pady=4)
        ttk.Button(pb, text="選択を承認", command=self.approve).pack(side="left")
        ttk.Button(pb, text="すべて承認", command=self.approve_all).pack(side="left", padx=4)
        ttk.Button(pb, text="選択を拒否", command=self.reject).pack(side="left")
        ttk.Button(pb, text="統合・分割の変更案を作成", command=self.suggest).pack(side="left", padx=12)

        cf = ttk.LabelFrame(right, text="実フォルダとの整合性", padding=4)
        cf.pack(fill="both", expand=True)
        self.mismatch = make_tree(cf, [("kind", "種類", 120), ("path", "パス", 220), ("message", "内容", 300),
                                       ("suggestion", "修正案", 300)], height=5)
        mb = ttk.Frame(cf)
        mb.pack(fill="x", pady=4)
        ttk.Button(mb, text="整合性チェック", command=self.check).pack(side="left")
        ttk.Button(mb, text="選択した修正案を適用（作成/改名のみ）", command=self.apply_fix).pack(side="left", padx=4)
        self._mismatches = {}
        self.refresh()

    # ---------- 表示 ----------
    def refresh(self) -> None:
        tax = self.engine.store.taxonomy
        self.version_var.set(f"分類体系 v{tax.taxonomy_version}（更新 {tax.updated_at}） 有効カテゴリ {len(tax.active())} 件")
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for c in tax.active():
            parent = c.parent_category_id if c.parent_category_id and self.tree.exists(c.parent_category_id) else ""
            self.tree.insert(parent, "end", iid=c.category_id, text=f"📁 {c.category_name}", open=True)
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel)
        self._props = {p.id: p for p in self.engine.db.list_proposals("pending")}
        kind = {"add": "追加", "merge": "統合", "split": "分割", "rename": "改名", "move": "階層"}
        fill_tree(self.props, [(p.id, (kind.get(p.kind, p.kind), p.title, p.reason, len(p.related_files)))
                               for p in self._props.values()])

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_detail(self) -> None:
        sel = self.tree.selection()
        c = self.engine.store.taxonomy.get(sel[0]) if sel else None
        if not c:
            return
        self._set_detail(
            f"カテゴリ: {c.category_name}\nID: {c.category_id}\nパス: {c.relative_path}\n"
            f"承認状態: {c.approval_status}\n説明: {c.description}\n"
            f"分類条件（キーワード）: {', '.join(c.keywords)}\n"
            f"除外条件: {', '.join(c.exclusion_rules) or 'なし'}\n"
            f"代表ファイル: {', '.join(c.examples[:5])}\n作成: {c.created_at}  更新: {c.updated_at}")

    def show_proposal(self) -> None:
        sel = self.props.selection()
        p = self._props.get(sel[0]) if sel else None
        if not p:
            return
        lines = [f"提案: {p.title}", f"理由: {p.reason}", f"作成: {p.created_at}"]
        for op in p.operations:
            if op.get("op") in ("add",):
                c = op["category"]
                lines += [f"説明: {c.get('description', '')}",
                          f"キーワード: {', '.join(c.get('classification_rules', {}).get('keywords', []))}"]
            elif op.get("op") == "split":
                lines += [f"分割後: {', '.join(ch['category_name'] for ch in op['children'])}"]
        if p.related_files:
            lines.append("関連ファイル: " + ", ".join(f.replace("\\", "/").split("/")[-1] for f in p.related_files[:8]))
        self._set_detail("\n".join(lines))

    def open_root(self) -> None:
        root = self.engine.organize_root
        root.mkdir(parents=True, exist_ok=True)
        open_path(root)

    # ---------- 提案 ----------
    def _approve(self, ids) -> None:
        if not ids:
            messagebox.showinfo("承認", "提案を選択してください")
            return

        def done(res):
            ok, msg = res
            (messagebox.showinfo if ok else messagebox.showerror)("承認", msg)
            self.on_change()

        self.ui.run(lambda: self.engine.approve_proposals(list(ids)), done)

    def approve(self) -> None:
        self._approve(self.props.selection())

    def approve_all(self) -> None:
        if self._props and messagebox.askyesno("承認", f"{len(self._props)} 件の提案をすべて承認しますか？"):
            self._approve(list(self._props))

    def reject(self) -> None:
        ids = self.props.selection()
        if ids and messagebox.askyesno("拒否", f"{len(ids)} 件の提案を拒否しますか？"):
            self.engine.reject_proposals(list(ids))
            self.on_change()

    def suggest(self) -> None:
        self.ui.run(self.engine.suggest_taxonomy_changes,
                    lambda ids: (messagebox.showinfo("変更案", f"{len(ids)} 件の変更案を作成しました" if ids
                                                     else "変更が必要な箇所は見つかりませんでした"), self.on_change()))

    # ---------- 直接編集（ユーザーが承認者） ----------
    def _selected_cat(self):
        sel = self.tree.selection()
        c = self.engine.store.taxonomy.get(sel[0]) if sel else None
        if not c:
            messagebox.showinfo("分類体系", "カテゴリを選択してください")
        return c

    def _change(self, ops, reason) -> None:
        ok, msg = self.engine.change_taxonomy(ops, reason)
        (messagebox.showinfo if ok else messagebox.showerror)("分類体系", msg + (
            "\n実フォルダとの差分は「整合性チェック」で確認できます。" if ok else ""))
        self.on_change()

    def rename(self) -> None:
        c = self._selected_cat()
        if not c:
            return
        name = simpledialog.askstring("名前を変更", "新しいカテゴリ名（カテゴリ ID は変わりません）",
                                      initialvalue=c.category_name, parent=self)
        if name and name != c.category_name:
            self._change([{"op": "rename", "category_id": c.category_id, "new_name": name}],
                         f"ユーザーが「{c.category_name}」を「{name}」に改名")

    def reparent(self) -> None:
        c = self._selected_cat()
        if not c:
            return
        options = {"（最上位）": None}
        options.update({x.relative_path: x.category_id for x in self.engine.store.taxonomy.active()
                        if x.category_id != c.category_id})
        win = tk.Toplevel(self)
        win.title("親カテゴリを変更")
        var = tk.StringVar(value="（最上位）")
        ttk.Combobox(win, textvariable=var, values=list(options), state="readonly", width=50).pack(padx=10, pady=10)

        def ok():
            win.destroy()
            self._change([{"op": "move", "category_id": c.category_id, "new_parent_id": options[var.get()]}],
                         f"ユーザーが「{c.category_name}」の階層を変更")

        ttk.Button(win, text="変更", command=ok).pack(pady=(0, 10))

    def edit_description(self) -> None:
        c = self._selected_cat()
        if not c:
            return
        desc = simpledialog.askstring("説明を編集", "カテゴリの目的・格納対象", initialvalue=c.description, parent=self)
        if desc is not None and desc != c.description:
            self._change([{"op": "update", "category_id": c.category_id, "description": desc}],
                         f"ユーザーが「{c.category_name}」の説明を更新")

    # ---------- 整合性 ----------
    def check(self) -> None:
        def done(res):
            self._mismatches = {str(i): m for i, m in enumerate(res)}
            label = {"missing_folder": "フォルダなし", "unknown_folder": "未登録フォルダ",
                     "renamed_folder": "改名未反映", "inactive_category_folder": "統合済みの残り",
                     "link_in_tree": "リンク"}
            fill_tree(self.mismatch, [(k, (label.get(m.kind, m.kind), m.path, m.message, m.suggestion))
                                      for k, m in self._mismatches.items()])
            if not res:
                messagebox.showinfo("整合性チェック", "実フォルダと分類体系は一致しています")

        self.ui.run(self.engine.consistency, done)

    def apply_fix(self) -> None:
        sel = [self._mismatches[i] for i in self.mismatch.selection() if i in self._mismatches]
        fixes = [m for m in sel if m.fix]
        if not fixes:
            messagebox.showinfo("修正", "自動で適用できる修正案（フォルダ作成・改名）を選択してください。\n"
                                       "削除や上書きを伴う修正は行いません。")
            return
        desc = "\n".join(f"・{m.suggestion}" for m in fixes)
        if not messagebox.askyesno("確認", f"次の修正を適用します:\n{desc}"):
            return
        msgs = []
        for m in fixes:
            try:
                msgs.append(self.engine.apply_consistency_fix(m.fix))
            except (ValueError, OSError) as e:
                msgs.append(f"失敗: {e}")
        messagebox.showinfo("修正", "\n".join(msgs))
        self.check()

"""ホーム画面: 4 つの手順で案内する。今やることだけを大きなボタンで示す。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from gui.common import UiBridge
from organizer.engine import Engine

ACCENT = "#2563eb"
DONE = "#16a34a"
MUTED = "#6b7280"
CARD_BG = "#ffffff"
PAGE_BG = "#f3f4f6"


def F(size: int, bold: bool = False) -> tuple:
    """アプリ全体の標準フォント（游ゴシック UI など）で、大きさと太さを指定する。"""
    family = tkfont.nametofont("TkDefaultFont").actual("family")
    return (family, size, "bold") if bold else (family, size)


class StepCard(tk.Frame):
    """手順 1 つ分のカード。番号・見出し・説明・状況・ボタンを持つ。"""

    def __init__(self, master, number: int, title: str, description: str) -> None:
        super().__init__(master, bg=CARD_BG, highlightthickness=2, highlightbackground="#e5e7eb", padx=18, pady=14)
        self.number = number
        head = tk.Frame(self, bg=CARD_BG)
        head.pack(fill="x")
        self.badge = tk.Label(head, text=str(number), width=3, bg="#e5e7eb", fg="#111827",
                              font=F(13, True))
        self.badge.pack(side="left")
        tk.Label(head, text=title, bg=CARD_BG, fg="#111827", font=F(14, True)).pack(
            side="left", padx=10)
        self.state_label = tk.Label(head, text="", bg=CARD_BG, fg=MUTED, font=F(11))
        self.state_label.pack(side="right")
        self.desc = tk.Label(self, text=description, bg=CARD_BG, fg="#374151", justify="left", wraplength=760,
                             font=F(11))
        self.desc.pack(anchor="w", pady=(8, 4))
        self.status = tk.Label(self, text="", bg=CARD_BG, fg="#111827", justify="left", wraplength=760,
                               font=F(11, True))
        self.status.pack(anchor="w", pady=(0, 8))
        self.buttons = tk.Frame(self, bg=CARD_BG)
        self.buttons.pack(anchor="w")
        self._texts = [self.desc, self.status]
        # 窓の幅に合わせて折り返す（固定幅だと「。」だけが次の行に落ちることがある）
        self.bind("<Configure>", lambda e: [t.configure(wraplength=max(300, e.width - 60)) for t in self._texts])

    def set_buttons(self, items: list[tuple[str, callable, bool]]) -> None:
        """items: (表示名, 押したときの処理, 目立たせるか)"""
        sig = tuple((t, p) for t, _, p in items)
        if sig == getattr(self, "_sig", None):
            return   # 表示が変わらないときは作り直さない（ちらつき・押し損じを防ぐ）
        self._sig = sig
        for w in self.buttons.winfo_children():
            w.destroy()
        for text, cmd, primary in items:
            ttk.Button(self.buttons, text=text, command=cmd,
                       style="Accent.TButton" if primary else "TButton").pack(side="left", padx=(0, 8))

    def set_state(self, state: str) -> None:
        """state: done（済み） / current（今やること） / todo（まだ）"""
        if state == "done":
            self.configure(highlightbackground="#e5e7eb")
            self.badge.configure(text="✓", bg=DONE, fg="white")
            self.state_label.configure(text="完了", fg=DONE)
        elif state == "current":
            self.configure(highlightbackground=ACCENT)
            self.badge.configure(text=str(self.number), bg=ACCENT, fg="white")
            self.state_label.configure(text="◀ 次はここ", fg=ACCENT)
        else:
            self.configure(highlightbackground="#e5e7eb")
            self.badge.configure(text=str(self.number), bg="#e5e7eb", fg="#111827")
            self.state_label.configure(text="", fg=MUTED)


class HomeTab(tk.Frame):
    def __init__(self, master, engine: Engine, ui: UiBridge, on_change, actions, goto) -> None:
        super().__init__(master, bg=PAGE_BG)
        self.engine = engine
        self.ui = ui
        self.on_change = on_change
        self.actions = actions      # 詳細タブ（フォルダ追加・提案・見張りの処理を持つ）
        self.goto = goto            # タブを切り替える関数
        inner = tk.Frame(self, bg=PAGE_BG, padx=24, pady=16)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text="ファイル整理", bg=PAGE_BG, fg="#111827", font=F(20, True)).pack(
            anchor="w")
        tk.Label(inner, text="上から順に進めてください。ファイルは、あなたが確認するまで勝手に移動しません。",
                 bg=PAGE_BG, fg="#374151", font=F(11)).pack(anchor="w", pady=(2, 12))

        self.cards = [
            StepCard(inner, 1, "整理するフォルダを選ぶ",
                     "ダウンロードフォルダなど、散らかりやすいフォルダを選びます。"),
            StepCard(inner, 2, "フォルダ分けを決める",
                     "AI がファイルの中身を読んで、フォルダ分けを提案します。気に入ったものを採用してください。"),
            StepCard(inner, 3, "確認して移動する",
                     "移動先を確認してから、まとめて移動します。移動はあとから元に戻せます。"),
            StepCard(inner, 4, "これから届くファイルを自動で整理する",
                     "選んだフォルダを見張り、新しいファイルを自動で仕分けます。迷ったファイルは移動せず、確認待ちにします。"),
        ]
        for c in self.cards:
            c.pack(fill="x", pady=6)
        self.footer = tk.Label(inner, text="", bg=PAGE_BG, fg=MUTED, font=F(10), justify="left")
        self.footer.pack(anchor="w", pady=(8, 0))
        self.refresh()

    # ---------- 状態の表示 ----------
    def refresh(self) -> None:
        e = self.engine
        st = e.stats()
        folders = e.settings.watch_folders
        cats = e.store.taxonomy.active()
        proposals = st["proposals"]

        done1 = bool(folders)
        done2 = bool(cats) and proposals == 0
        done3 = done2 and st["ready"] == 0 and st["moved"] > 0
        done4 = st["monitoring"] and not st["paused"]
        states = [done1, done2, done3, done4]
        current = next((i for i, d in enumerate(states) if not d), None)
        for i, card in enumerate(self.cards):
            card.set_state("done" if states[i] else ("current" if i == current else "todo"))

        # 1. フォルダ
        c = self.cards[0]
        if folders:
            names = "、".join(w.path for w in folders[:3]) + (" ほか" if len(folders) > 3 else "")
            c.status.configure(text=f"選んだフォルダ: {names}")
        else:
            c.status.configure(text="まだフォルダが選ばれていません。")
        c.set_buttons([("フォルダを選ぶ…", self.actions.add_folder, current == 0)])

        # 2. フォルダ分け
        c = self.cards[1]
        if proposals:
            c.status.configure(text=f"AI の提案が {proposals} 件あります。内容を見て採用してください。")
            c.set_buttons([("提案を見る", lambda: self.goto("taxonomy"), current == 1),
                           ("もう一度提案してもらう", self.actions.propose, False)])
        elif cats:
            c.status.configure(text=f"{len(cats)} 個のフォルダに分けて整理します。")
            c.set_buttons([("フォルダ分けを見る", lambda: self.goto("taxonomy"), False),
                           ("AI に見直してもらう", self.actions.propose, False)])
        else:
            c.status.configure(text="まだフォルダ分けが決まっていません。")
            c.set_buttons([("AI に提案してもらう", self._propose, current == 1 and done1)])

        # 3. 確認して移動
        c = self.cards[2]
        parts = []
        if st["ready"]:
            parts.append(f"移動待ち {st['ready']} 件")
        if st["review"]:
            parts.append(f"確認が必要 {st['review']} 件")
        if st["moved"]:
            parts.append(f"移動済み {st['moved']} 件")
        c.status.configure(text="、".join(parts) if parts else "まだ移動するファイルはありません。")
        c.set_buttons([("確認して移動する", lambda: self.goto("review"), current == 2 and done2),
                       ("元に戻す（履歴）", lambda: self.goto("history"), False)])

        # 4. 自動整理
        c = self.cards[3]
        if st["monitoring"] and not st["paused"]:
            mode = ("新しいファイルは、確実なものだけ自動で移動します。" if e.settings.auto_move and not e.settings.preview_mode
                    else "新しいファイルは仕分けだけ行い、移動はあなたの確認後にします。")
            c.status.configure(text=f"見張り中です。{mode}")
            c.set_buttons([("見張りを止める", self._stop, False)])
        else:
            c.status.configure(text="止まっています。")
            c.set_buttons([("見張りを始める", self._start, current == 3)])

        self.footer.configure(
            text=f"AI: {self._ai_label()} ／ 整理先: {e.settings.organize_root}"
                 + ("　⏳ 処理中…" if self.ui.busy else ""))

    def _ai_label(self) -> str:
        name = getattr(self.engine.classifier, "name", "")
        return {"semantic": "ローカル AI（意味の近さで判断）", "ollama": "ローカル AI",
                "heuristic": "AI なし（ファイル中の言葉で判断）"}.get(name, name)

    # ---------- 操作 ----------
    def _propose(self) -> None:
        if not self.engine.settings.watch_folders:
            self.actions.add_folder()
            return
        self.actions.propose()

    def _start(self) -> None:
        self.actions.start()
        self.on_change()

    def _stop(self) -> None:
        self.actions.stop()
        self.on_change()

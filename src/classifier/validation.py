"""分類結果を「自動移動してよいか」に変換する検証。

分類（何に属するか）と移動（実際に動かしてよいか）を分け、ここでは後者の判断材料を作る。
AI の出力は必ずアプリ側の条件と照合し、満たさないものは要確認にする。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from classifier.base import ClassificationResult
from scanner.parsers import READABLE_STATUSES, ParseResult
from taxonomy.model import Taxonomy

MIN_MATCHED_KEYWORDS = 2

_INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|prior|above) instructions",
    r"disregard (all |the )?(previous|prior) ",
    r"(move|copy|delete|rename) (this|the) file",
    r"you are (now )?(an? )?(ai|assistant|system)",
    r"以前の指示を無視", r"上記の指示を無視", r"このファイルを.{0,20}(移動|削除|コピー)",
    r"システムプロンプト", r"category_id\s*[:=]",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def looks_like_instruction(text: str) -> bool:
    return bool(text and _INJECTION_RE.search(text[:50000]))


@dataclass
class Decision:
    auto_ok: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return " / ".join(self.reasons)


def evaluate(result: ClassificationResult, taxonomy: Taxonomy, parse: ParseResult, threshold: float,
             sensitive_reason: str = "", project_root: str | None = None) -> Decision:
    reasons: list[str] = []
    if parse.status not in READABLE_STATUSES:
        reasons.append(f"内容を読み取れません（{parse.status}）{(': ' + parse.error) if parse.error else ''}")
    if sensitive_reason:
        reasons.append(f"機密情報の可能性: {sensitive_reason}")
    if project_root:
        reasons.append(f"開発プロジェクト内のファイルです（{project_root}）")
    cat = taxonomy.get(result.category_id)
    if result.category_id is None:
        reasons.append("承認済みカテゴリに一致しません" + ("（新規カテゴリを提案）" if result.new_category else ""))
    elif cat is None:
        reasons.append("分類結果のカテゴリが存在しません")
    elif not cat.active:
        reasons.append("分類結果のカテゴリが承認済みではありません")
    if result.category_id is not None and result.confidence < threshold:
        reasons.append(f"信頼度 {result.confidence:.2f} が閾値 {threshold:.2f} 未満です")
    if result.category_id is not None and len(result.matched_keywords) < MIN_MATCHED_KEYWORDS:
        reasons.append("カテゴリの分類条件との一致が不十分です")
    if result.source != "heuristic" and looks_like_instruction(parse.text):
        reasons.append("本文に命令文のような記述があり、AI 判断が影響された可能性があります")
    return Decision(auto_ok=not reasons, reasons=reasons)

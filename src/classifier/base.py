"""分類器の共通データ型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scanner.features import DocFeatures
from scanner.parsers import ParseResult


@dataclass
class DocInput:
    path: Path
    features: DocFeatures
    parse: ParseResult
    size: int = 0
    mtime: float = 0.0

    @property
    def snippet(self) -> str:
        return (self.parse.text or "")[:1500]


@dataclass
class CategorySuggestion:
    name: str
    description: str
    keywords: list[str]
    keyword_weights: dict[str, float] = field(default_factory=dict)
    parent_category_id: str | None = None
    examples: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    category_id: str | None
    confidence: float
    reasons: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    new_category: CategorySuggestion | None = None
    source: str = "heuristic"
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def reason_text(self) -> str:
        return " / ".join(self.reasons)


@dataclass
class DraftCategory:
    """提案中のカテゴリ。category_id は承認後もそのまま使われる。"""
    category_id: str
    name: str
    description: str
    keywords: list[str]
    keyword_weights: dict[str, float]
    parent_category_id: str | None
    examples: list[str]
    members: list[str]           # ファイルパス

    def to_spec(self) -> dict:
        return {
            "category_id": self.category_id,
            "category_name": self.name,
            "parent_category_id": self.parent_category_id,
            "description": self.description,
            "classification_rules": {"keywords": self.keywords, "keyword_weights": self.keyword_weights},
            "exclusion_rules": [],
            "examples": self.examples,
        }


@dataclass
class TaxonomyDraft:
    new_categories: list[DraftCategory] = field(default_factory=list)
    reused: dict[str, str] = field(default_factory=dict)   # path -> 既存 category_id
    unassigned: list[str] = field(default_factory=list)
    source: str = "heuristic"
    notes: list[str] = field(default_factory=list)


class Classifier:
    name = "base"

    def propose_taxonomy(self, docs: list[DocInput], taxonomy) -> TaxonomyDraft:  # pragma: no cover
        raise NotImplementedError

    def classify(self, doc: DocInput, taxonomy) -> ClassificationResult:  # pragma: no cover
        raise NotImplementedError

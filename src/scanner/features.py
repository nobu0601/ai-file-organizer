"""ファイル名・パス・本文からの特徴語抽出（日本語/英語対応の軽量版）。

外部の形態素解析器を必須にしないため、以下の単純な規則で語を切り出す。
- 英数字の語（3 文字以上、camelCase / snake_case を分割）
- カタカナ語（2 文字以上）
- 漢字の連続（2 文字以上。長い連続は 2〜4 文字の部分語も追加）
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+|[ァ-ヴー]{2,}|[一-龠々]{2,}")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")

STOPWORDS = {
    # 英語
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "you", "your",
    "not", "but", "all", "can", "has", "have", "had", "will", "would", "should", "into", "about",
    "then", "than", "they", "them", "their", "there", "what", "when", "which", "while", "who",
    "how", "its", "our", "out", "use", "used", "using", "via", "per", "also", "any", "each",
    "file", "files", "copy", "final", "new", "old", "draft", "untitled", "document", "doc",
    "page", "data", "none", "true", "false", "null", "self", "return", "import", "def", "class",
    "int", "str", "var", "let", "const", "function", "public", "private", "static", "void",
    "include", "std", "string", "http", "https", "www", "com", "html", "pdf", "xlsx", "docx",
    "pptx", "csv", "txt", "json", "xml", "yaml", "yml", "png", "jpg", "jpeg", "log", "md",
    "sheet", "sheet1", "info", "debug", "warning", "error", "value", "name", "type", "id",
    "downloads", "download", "desktop", "documents", "users", "user", "home", "temp", "tmp",
    # 日本語
    "ファイル", "データ", "ドキュメント", "コピー", "以下", "以上", "場合", "ため", "こと",
    "もの", "よう", "これ", "それ", "あれ", "本日", "今回", "前回", "について", "および",
    "資料", "一覧", "対象", "内容", "実施", "確認", "記載", "作成", "使用", "必要",
    # ファイル名に付きやすい汎用語（テーマを表さない）
    "メモ", "ノート", "まとめ", "リスト", "シート", "下書", "下書き", "無題", "新規", "テキスト", "ワークシート", "エクスポート",
    "スキャン", "バージョン", "最新", "修正", "版", "scan", "export", "output", "untitled", "memo", "note",
    "notes", "summary", "report", "list", "sample", "test1", "image", "photo", "microsoft", "excel", "word",
}

# ファイル名から読み取る語は本文より重い（プロジェクト名などが出やすい）
WEIGHT_NAME = 3.0
WEIGHT_FOLDER = 2.0
WEIGHT_TEXT = 1.0
# 漢字の部分語（長い複合語から切り出した 2〜3 文字）は補助的な手掛かりとして軽く扱う
SUBWORD_FACTOR = 0.35


def tokenize(text: str) -> list[str]:
    """全語＋漢字の部分語。部分語を区別したい場合は tokenize_split を使う。"""
    whole, subs = tokenize_split(text)
    return whole + subs


def tokenize_split(text: str) -> tuple[list[str], list[str]]:
    out: list[str] = []
    subs: list[str] = []
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        if w[0].isascii():
            pieces = _CAMEL.sub(" ", w).split()
            for p in pieces + ([w] if len(pieces) > 1 else []):
                p = p.lower()
                if len(p) >= 3 and p not in STOPWORDS and not p.isdigit():
                    out.append(p)
        elif "一" <= w[0] <= "龠" or w[0] == "々":
            if w not in STOPWORDS:
                out.append(w)
            # 複合語の一部が一致すれば関連する（例: 「領収書」と「領収」、「工事日程」と「工事」）
            if len(w) >= 3:
                for n in ((2, 3) if len(w) >= 4 else (2,)):
                    for i in range(0, len(w) - n + 1):
                        sub = w[i:i + n]
                        if sub not in STOPWORDS:
                            subs.append(sub)
        else:
            if w not in STOPWORDS and w.strip("ー"):
                out.append(w)
            if len(w) >= 4:
                for i in range(0, len(w) - 1):
                    sub = w[i:i + 2]
                    if "ー" not in sub[0] and sub not in STOPWORDS:
                        subs.append(sub)
    return out, subs


def _clean_stem(path: Path) -> str:
    return re.sub(r"[_\-.\s()（）\[\]【】]+", " ", Path(path).stem)


def name_tokens(path: Path) -> list[str]:
    return tokenize(_clean_stem(path))


@dataclass
class DocFeatures:
    path: str
    name: str
    ext: str
    folder_tokens: list[str]
    name_tokens: list[str]
    text_tokens: list[str]
    weights: dict[str, float] = field(default_factory=dict)
    # 元の語として現れた語（漢字・カタカナの 2 文字の切れ端を含まない）。表示用の語はここから選ぶ
    whole_terms: set[str] = field(default_factory=set)

    def top_terms(self, n: int = 10) -> list[str]:
        return [t for t, _ in sorted(self.weights.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def build_features(path: Path, text: str, watch_root: Path | None = None,
                   max_text_tokens: int = 4000) -> DocFeatures:
    path = Path(path)
    folders: list[str] = []
    if watch_root is not None:
        try:
            rel_parent = path.parent.relative_to(watch_root)
            for part in rel_parent.parts:
                folders.extend(tokenize(part))
        except ValueError:
            pass
    n_whole, n_sub = tokenize_split(_clean_stem(path))
    t_whole, t_sub = tokenize_split(text)
    t_whole, t_sub = t_whole[:max_text_tokens], t_sub[:max_text_tokens]
    ntoks = n_whole + n_sub
    ttoks = t_whole + t_sub
    weights: Counter[str] = Counter()
    for t in n_whole:
        weights[t] += WEIGHT_NAME
    for t in n_sub:
        weights[t] += WEIGHT_NAME * SUBWORD_FACTOR
    for t in folders:
        weights[t] += WEIGHT_FOLDER
    # 本文の語は出現回数の対数で抑える（長文が名前の情報を押し流さないように）
    for toks, factor in ((t_whole, 1.0), (t_sub, SUBWORD_FACTOR)):
        for t, c in Counter(toks).items():
            weights[t] += WEIGHT_TEXT * factor * (1.0 + math.log(c))
    return DocFeatures(
        path=str(path), name=path.name, ext=path.suffix.lower(), folder_tokens=folders,
        name_tokens=ntoks, text_tokens=ttoks, weights=dict(weights),
        whole_terms=set(n_whole) | set(t_whole) | set(folders),
    )


def tfidf_vectors(docs: list[DocFeatures]) -> list[dict[str, float]]:
    n = len(docs)
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d.weights.keys())
    vecs = []
    for d in docs:
        v = {}
        for t, w in d.weights.items():
            # 全ファイルに共通する語は区別に役立たないため、ほぼ 0 になるようにする
            idf = math.log((1 + n) / (1 + df[t])) + 0.1
            v[t] = w * idf
        vecs.append(normalize(v))
    return vecs


def normalize(v: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(x * x for x in v.values()))
    if norm == 0:
        return {}
    return {k: x / norm for k, x in v.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())

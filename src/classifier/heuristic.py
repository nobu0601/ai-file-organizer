"""ローカル LLM が使えない場合でも動く内容ベースの分類器。

カテゴリを開発者が固定定義せず、ファイル群の語彙（ファイル名・フォルダ名・本文）の
TF-IDF 類似度でクラスタリングし、クラスタの特徴語から分類体系を考案する。
拡張子は分類の手掛かりに使わない（説明文に形式の内訳を書くだけ）。
"""
from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from pathlib import Path

from classifier.base import (CategorySuggestion, ClassificationResult, Classifier, DocInput, DraftCategory,
                             TaxonomyDraft)
from scanner.features import cosine, normalize, tfidf_vectors
from taxonomy.model import Category, Taxonomy, new_category_id

CLUSTER_THRESHOLD = 0.05
SUBCLUSTER_THRESHOLD = 0.34
MIN_CLUSTER = 2
SUBDIVIDE_MIN = 6
REUSE_CONFIDENCE = 0.6
NEW_CATEGORY_BELOW = 0.45
PROFILE_TERMS = 40
SIM_FULL = 0.2          # この類似度以上を「十分に似ている」とみなす
MIN_SUGGEST_SIM = 0.03  # これ未満は候補としても出さない


class HeuristicClassifier(Classifier):
    name = "heuristic"

    # ---------- 既存カテゴリへの分類 ----------
    def score_category(self, doc: DocInput, cat: Category) -> tuple[float, list[str], float]:
        """(cosine 類似度, 一致キーワード, 一致キーワードの重み割合) を返す。"""
        weights = doc.features.weights
        tokens = set(weights)
        for ex in cat.exclusion_rules:
            if ex.lower() in tokens or ex in tokens:
                return 0.0, [], 0.0
        kw = cat.keyword_weights
        if not kw:
            return 0.0, [], 0.0
        dvec = normalize(_damp(weights))
        cvec = normalize(kw)
        sim = cosine(dvec, cvec)
        matched = [k for k in kw if k in tokens]
        total = sum(kw.values()) or 1.0
        coverage = sum(kw[k] for k in matched) / total
        pats = cat.classification_rules.get("filename_patterns") or []
        if any(fnmatch.fnmatch(doc.path.name.lower(), p.lower()) for p in pats):
            sim = min(1.0, sim + 0.15)
            coverage = min(1.0, coverage + 0.2)
        return sim, matched, coverage

    def classify(self, doc: DocInput, taxonomy: Taxonomy) -> ClassificationResult:
        scored = []
        for cat in taxonomy.active():
            sim, matched, coverage = self.score_category(doc, cat)
            scored.append((sim, cat, matched, coverage))
        scored.sort(key=lambda x: (-x[0], -len(x[2]), x[1].category_name))
        if not scored or scored[0][0] < MIN_SUGGEST_SIM or not scored[0][2]:
            result = ClassificationResult(category_id=None, confidence=0.0,
                                          reasons=["既存カテゴリの分類条件に一致しません"])
        else:
            sim, cat, matched, coverage = scored[0]
            second = scored[1][0] if len(scored) > 1 else 0.0
            conf = self._confidence(sim, len(matched), second)
            reasons = [
                f"カテゴリ「{cat.category_name}」の特徴語と一致: {', '.join(matched[:6])}",
                f"語彙類似度 {sim:.2f}（次点 {second:.2f}）、一致 {len(matched)} 語",
            ]
            if conf < 0.5:
                reasons.append("一致が弱いため候補として表示します")
            result = ClassificationResult(
                category_id=cat.category_id, confidence=round(conf, 3), reasons=reasons,
                matched_keywords=matched, scores={c.category_id: round(x, 3) for x, c, _, _ in scored[:5]},
            )
        if result.confidence < NEW_CATEGORY_BELOW:
            sug = self.suggest_category(doc)
            if sug:
                result.new_category = sug
                result.reasons.append(f"新規カテゴリ「{sug.name}」も提案")
        return result

    @staticmethod
    def _confidence(sim: float, matched: int, second: float = 0.0) -> float:
        """類似度・一致語数・次点との差から信頼度を出す（0〜1）。

        語が 1〜2 語しか一致しない場合は偶然の一致があり得るため上限を設け、
        自動移動の閾値（初期 0.85）に届かないようにする。
        """
        if matched == 0 or sim <= 0:
            return 0.0
        margin = max(0.0, 1.0 - second / sim)
        c = 0.55 * min(1.0, sim / SIM_FULL) + 0.25 * min(1.0, matched / 3) + 0.20 * margin
        if matched < 2:
            c = min(c, 0.5)
        elif matched < 3:
            c = min(c, 0.8)
        return min(1.0, c)

    def suggest_category(self, doc: DocInput) -> CategorySuggestion | None:
        terms = _rank_terms_single(doc)
        if len(terms) < 2:
            return None
        name = make_name(terms[:2])
        top = terms[:8]
        mx = max(doc.features.weights.get(t, 1.0) for t in top) or 1.0
        return CategorySuggestion(
            name=name,
            description=describe(terms[:3], [doc.path.name], [doc.features.ext]),
            keywords=top,
            keyword_weights={t: round(doc.features.weights.get(t, 1.0) / mx, 3) for t in top},
            examples=[doc.path.name],
        )

    # ---------- 分類体系の考案 ----------
    def propose_taxonomy(self, docs: list[DocInput], taxonomy: Taxonomy) -> TaxonomyDraft:
        draft = TaxonomyDraft(source=self.name)
        remaining: list[DocInput] = []
        for d in docs:
            if taxonomy.active():
                r = self.classify(d, taxonomy)
                if r.category_id and r.confidence >= REUSE_CONFIDENCE:
                    draft.reused[str(d.path)] = r.category_id
                    continue
            remaining.append(d)
        if not remaining:
            return draft
        used_names = {c.category_name.lower() for c in taxonomy.active()}
        self._build(remaining, None, 0, draft, used_names)
        assigned = {m for c in draft.new_categories for m in c.members}
        draft.unassigned = [str(d.path) for d in remaining if str(d.path) not in assigned]
        if draft.unassigned:
            draft.notes.append(f"{len(draft.unassigned)} 件は他のファイルと共通点が少なく、カテゴリを作りませんでした")
        return draft

    def _build(self, docs: list[DocInput], parent_id: str | None, depth: int, draft: TaxonomyDraft,
               used_names: set[str]) -> None:
        threshold = CLUSTER_THRESHOLD if depth == 0 else SUBCLUSTER_THRESHOLD + 0.08 * (depth - 1)
        clusters = cluster(docs, threshold)
        for members in clusters:
            if len(members) < MIN_CLUSTER:
                continue
            terms = rank_terms(members, docs)
            if len(terms) < 1:
                continue
            name = unique_name(make_name(terms[:2]), used_names, terms)
            used_names.add(name.lower())
            top = terms[:8]
            weights = profile_weights(members, docs, top)
            cid = new_category_id()
            cat = DraftCategory(
                category_id=cid, name=name,
                description=describe(terms[:3], [m.path.name for m in members], [m.features.ext for m in members]),
                keywords=top, keyword_weights=weights, parent_category_id=parent_id,
                examples=[m.path.name for m in members[:5]], members=[str(m.path) for m in members],
            )
            draft.new_categories.append(cat)
            # 大きなクラスタは、さらにまとまりが見つかる場合だけ子カテゴリに分ける
            if len(members) >= SUBDIVIDE_MIN and depth < 2:
                sub = cluster(members, SUBCLUSTER_THRESHOLD + 0.08 * depth)
                big = [s for s in sub if len(s) >= MIN_CLUSTER]
                covered = sum(len(s) for s in big)
                if len(big) >= 2 and covered >= 0.6 * len(members):
                    before = {c.category_id for c in draft.new_categories}
                    self._build(members, cid, depth + 1, draft, set(used_names))
                    for c in draft.new_categories:
                        if c.category_id not in before:
                            used_names.add(c.name.lower())
                    child_members = {m for c in draft.new_categories if c.parent_category_id == cid
                                     for m in c.members}
                    cat.members = [m for m in cat.members if m not in child_members]


# ---------- 補助関数 ----------
def _damp(weights: dict[str, float]) -> dict[str, float]:
    # 長文の頻出語が支配しないよう平方根で抑える
    return {k: math.sqrt(v) for k, v in weights.items()}


def cluster(docs: list[DocInput], threshold: float) -> list[list[DocInput]]:
    """決定的なセントロイド型クラスタリング＋クラスタ同士の再統合。"""
    if not docs:
        return []
    order = sorted(range(len(docs)), key=lambda i: str(docs[i].path).lower())
    vecs = tfidf_vectors([d.features for d in docs])
    groups: list[list[int]] = []
    cents: list[dict[str, float]] = []
    for i in order:
        best, best_sim = -1, 0.0
        for g, c in enumerate(cents):
            s = cosine(vecs[i], c)
            if s > best_sim:
                best, best_sim = g, s
        if best >= 0 and best_sim >= threshold:
            groups[best].append(i)
            cents[best] = _centroid([vecs[j] for j in groups[best]])
        else:
            groups.append([i])
            cents.append(dict(vecs[i]))
    merged = True
    while merged:
        merged = False
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                if cosine(cents[a], cents[b]) >= threshold * 1.2:
                    groups[a].extend(groups[b])
                    cents[a] = _centroid([vecs[j] for j in groups[a]])
                    del groups[b], cents[b]
                    merged = True
                    break
            if merged:
                break
    # 1 件だけのグループは、近いクラスタがあれば吸収する
    singles = [g for g in groups if len(g) == 1]
    multi = [g for g in groups if len(g) > 1]
    if multi:
        mcents = [_centroid([vecs[j] for j in g]) for g in multi]
        rest = []
        for g in singles:
            i = g[0]
            sims = [cosine(vecs[i], c) for c in mcents]
            k = max(range(len(sims)), key=lambda x: sims[x])
            if sims[k] >= threshold * 0.6:
                multi[k].append(i)
            else:
                rest.append(g)
        groups = multi + rest
    out = [[docs[i] for i in sorted(g, key=lambda j: str(docs[j].path).lower())] for g in groups]
    out.sort(key=lambda g: (-len(g), str(g[0].path).lower()))
    return out


def _centroid(vs: list[dict[str, float]]) -> dict[str, float]:
    acc: Counter[str] = Counter()
    for v in vs:
        acc.update(v)
    return normalize(dict(acc))


def rank_terms(members: list[DocInput], universe: list[DocInput]) -> list[str]:
    """クラスタを特徴づける語を、メンバー内の出現率と全体での希少さで順位付けする。"""
    n = len(members)
    df_in: Counter[str] = Counter()
    w_in: Counter[str] = Counter()
    for m in members:
        df_in.update(m.features.weights.keys())
        for t, w in m.features.weights.items():
            w_in[t] += math.sqrt(w)
    df_all: Counter[str] = Counter()
    for d in universe:
        df_all.update(d.features.weights.keys())
    N = len(universe)
    scored = []
    for t, c in df_in.items():
        if n >= 2 and c < 2:
            continue
        share = c / n
        rarity = math.log((1 + N) / (1 + df_all[t])) + 1.0
        name_bonus = 1.3 if any(t in m.features.name_tokens for m in members) else 1.0
        scored.append((share * rarity * name_bonus * (w_in[t] / n), t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    terms = [t for _, t in scored]
    return _dedupe_substrings(terms)


def _rank_terms_single(doc: DocInput) -> list[str]:
    w = doc.features.weights
    terms = [t for t, _ in sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))]
    return _dedupe_substrings(terms)


def _dedupe_substrings(terms: list[str]) -> list[str]:
    out: list[str] = []
    for t in terms:
        if any((t in o or o in t) and t != o for o in out[:4]):
            continue
        out.append(t)
    return out


def profile_weights(members: list[DocInput], universe: list[DocInput], top: list[str],
                    size: int = PROFILE_TERMS) -> dict[str, float]:
    """カテゴリの特徴（語と重み）。表示用の上位語だけでなく、所属ファイル全体の語の分布を持たせる。

    言い回しの違う新しいファイルでも、どこかの語が一致すれば分類できるようにするため。
    """
    vecs = tfidf_vectors([d.features for d in universe])
    idx = {str(d.path): i for i, d in enumerate(universe)}
    cent = _centroid([vecs[idx[str(m.path)]] for m in members if str(m.path) in idx])
    ranked = sorted(cent.items(), key=lambda kv: (-kv[1], kv[0]))[:size]
    w = dict(ranked)
    for t in top:   # 表示用の分類条件の語は必ず含める
        w.setdefault(t, cent.get(t, 0.0))
    mx = max(w.values()) if w else 1.0
    floor = 0.05
    return {t: round(max(v / mx, floor), 3) for t, v in w.items()}


def cluster_weights(members: list[DocInput], top: list[str]) -> dict[str, float]:
    n = len(members)
    w = {t: sum(1 for m in members if t in m.features.weights) / n for t in top}
    mx = max(w.values()) if w else 1.0
    return {t: round(v / mx, 3) for t, v in w.items()}


def make_name(terms: list[str]) -> str:
    parts = []
    for t in terms:
        if re.fullmatch(r"[a-z0-9]+", t):
            parts.append(t.upper() if len(t) <= 3 else t.capitalize())
        else:
            parts.append(t)
    return " ".join(parts)[:60] or "未命名"


def unique_name(name: str, used: set[str], terms: list[str]) -> str:
    if name.lower() not in used:
        return name
    for extra in terms[2:5]:
        cand = f"{name} {make_name([extra])}"
        if cand.lower() not in used:
            return cand
    i = 2
    while f"{name} {i}".lower() in used:
        i += 1
    return f"{name} {i}"


def describe(terms: list[str], names: list[str], exts: list[str]) -> str:
    kinds = Counter(e.lstrip(".").upper() or "拡張子なし" for e in exts)
    kind_txt = "、".join(f"{k} {v}件" for k, v in kinds.most_common(5))
    kw = "」「".join(terms)
    return f"「{kw}」に関連するファイルをまとめるカテゴリ。形式を問わず同じテーマ・用途のものを格納する（考案時 {len(names)}件: {kind_txt}）。"

"""埋め込みモデル（意味の近さ）による分類。

Ollama の埋め込みモデル（既定: bge-m3）で文書を数値ベクトルにし、
- 分類体系の考案: 意味の近いファイル同士をまとめる（平均連結の凝集型クラスタリング）
- 新しいファイルの分類: 各カテゴリのメンバーの平均ベクトルとの近さで決める
を行う。語が一致しなくても意味が近ければまとめられる点が内容ベース分類との違い。

安全のため:
- 自動移動は、内容ベース分類の 1 位と一致した場合だけ（cross_checked）
- 意味の近さも語の一致も弱いものは、候補を出さない
- 埋め込みが使えないときは内容ベース分類に戻る
"""
from __future__ import annotations

import logging

from classifier.base import ClassificationResult, Classifier, DocInput, DraftCategory, TaxonomyDraft
from classifier.heuristic import (AGREE_MIN_CONFIDENCE, MIN_CLUSTER, HeuristicClassifier, describe, make_name, profile_weights,
                                  rank_terms, unique_name)
from classifier.ollama import OllamaClient, OllamaError
from history.db import path_key
from history.vectors import VectorStore, dot, mean, normalize
from taxonomy.model import Taxonomy, new_category_id

log = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "bge-m3"
# bge-m3 の類似度（cos）の目安。関係のない文書同士でも 0.45〜0.55 程度になる
CLUSTER_SIM = 0.54           # これ以上似ているものを同じカテゴリにまとめる（平均連結）
SUBCLUSTER_SIM = 0.60        # 大きなカテゴリを下位カテゴリに分ける基準
SUBDIVIDE_MIN = 8
SIM_FLOOR = 0.50             # 信頼度 0 とみなす類似度
SIM_SPAN = 0.15              # SIM_FLOOR からこれだけ上で「十分に近い」
MARGIN_FULL = 0.06           # 次点との差がこれ以上なら「迷いがない」
# これ未満は候補を出さない（関係のない文書でも 0.5〜0.6 程度になるため。評価データ A・B の実測から決めた）
MIN_SUGGEST_SIM = 0.60
FALLBACK_HEURISTIC_CONF = 0.55   # 意味の近さがはっきりしないとき、語の一致の候補を出す下限
REUSE_CONFIDENCE = 0.6
LLM_SKIP_CONFIDENCE = 0.85   # 埋め込みの信頼度がこれ以上なら LLM に聞かない（速さのため）
LLM_CANDIDATES = 4           # LLM に見せる候補の数（埋め込みで絞り込む）
MAX_CLUSTER_DOCS = 400       # 考案時にクラスタリングする最大件数（残りは近いカテゴリへ割り当て）
BATCH = 16


def doc_text(doc: DocInput) -> str:
    return f"ファイル名: {doc.path.name}\n{(doc.parse.text or '')[:1000]}"


class SemanticClassifier(Classifier):
    name = "semantic"

    def __init__(self, client: OllamaClient, embed_model: str = DEFAULT_EMBED_MODEL,
                 vectors: VectorStore | None = None, heuristic: HeuristicClassifier | None = None,
                 namer=None) -> None:
        self.client = client
        self.model = embed_model
        self.vectors = vectors
        self.heuristic = heuristic or HeuristicClassifier()
        self.namer = namer            # OllamaClassifier（カテゴリ名を LLM に考えてもらう場合）
        self.last_error = ""

    def bind_store(self, vectors: VectorStore) -> None:
        self.vectors = vectors

    # ---------- 埋め込み ----------
    def embed_docs(self, docs: list[DocInput]) -> list[list[float]]:
        out: list[list[float] | None] = [None] * len(docs)
        todo = []
        for i, d in enumerate(docs):
            cached = self.vectors.get_cached(path_key(d.path), self.model, d.size, d.mtime) if self.vectors else None
            if cached is not None:
                out[i] = cached
            else:
                todo.append(i)
        for k in range(0, len(todo), BATCH):
            chunk = todo[k:k + BATCH]
            vecs = self.client.embed([doc_text(docs[i]) for i in chunk], self.model)
            for i, v in zip(chunk, vecs):
                v = normalize(v)
                out[i] = v
                if self.vectors:
                    self.vectors.put_cached(path_key(docs[i].path), self.model, docs[i].size, docs[i].mtime, v)
        return out  # type: ignore[return-value]

    # ---------- 学習（承認・移動されたファイルをカテゴリの特徴に加える） ----------
    def learn(self, category_id: str, docs: list[DocInput], source: str) -> None:
        if not self.vectors or not docs:
            return
        try:
            vecs = self.embed_docs(docs)
        except OllamaError as e:
            self.last_error = str(e)
            log.warning("埋め込みを作れないため学習を飛ばします: %s", e)
            return
        for d, v in zip(docs, vecs):
            self.vectors.add_member(category_id, path_key(d.path), self.model, v, source)

    def forget(self, path: str) -> None:
        if self.vectors:
            self.vectors.remove_member(path_key(path))

    def on_operations(self, operations: list[dict]) -> None:
        if not self.vectors:
            return
        for op in operations:
            if op.get("op") == "merge":
                for sid in op.get("source_ids", []):
                    self.vectors.reassign(sid, op["target_id"])

    # ---------- 分類 ----------
    def _ranked(self, vec: list[float], taxonomy: Taxonomy) -> list[tuple[float, object]]:
        out = []
        for c in taxonomy.active():
            cen = self.vectors.centroid(c.category_id, self.model) if self.vectors else []
            if cen:
                out.append((dot(vec, cen), c))
        out.sort(key=lambda x: (-x[0], x[1].category_name))
        return out

    @staticmethod
    def _confidence(sim: float, margin: float) -> float:
        s = min(1.0, max(0.0, (sim - SIM_FLOOR) / SIM_SPAN))
        m = min(1.0, max(0.0, margin / MARGIN_FULL))
        return round(0.6 * s + 0.4 * m, 3)

    def classify(self, doc: DocInput, taxonomy: Taxonomy) -> ClassificationResult:
        base = self.heuristic.classify(doc, taxonomy)
        try:
            vec = self.embed_docs([doc])[0]
        except OllamaError as e:
            self.last_error = str(e)
            base.reasons.append("（埋め込みモデルが利用できないため内容ベース分類）")
            return base
        ranked = self._ranked(vec, taxonomy)
        if not ranked:
            return base
        sim, cat = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else SIM_FLOOR
        margin = sim - second
        weak_meaning = sim < MIN_SUGGEST_SIM
        conf = self._confidence(sim, margin)
        notes: list[str] = []
        llm_used = False

        # 埋め込みだけでは決めきれないとき、または内容ベース分類と判断が割れたときに、
        # LLM に候補の中から選ばせる
        heuristic_agrees = base.category_id == cat.category_id and base.confidence >= AGREE_MIN_CONFIDENCE
        if self.namer is not None and (weak_meaning or conf < LLM_SKIP_CONFIDENCE or not heuristic_agrees):
            cands = [(s_, c) for s_, c in ranked[:LLM_CANDIDATES] if s_ >= SIM_FLOOR]
            if cands:
                try:
                    idx, llm_conf, reason = self.namer.pick(doc, [c for _, c in cands])
                    llm_used = True
                except OllamaError as e:
                    self.last_error = str(e)
                    idx = -1
                # 埋め込みが確信しているのに内容ベースと割れたため LLM に確認した場合（確認のみ）
                confirm_only = not weak_meaning and conf >= LLM_SKIP_CONFIDENCE
                if llm_used and idx is None:
                    notes.append(f"LLM: 候補のどれにも当てはまらない（{reason}）")
                    if confirm_only or weak_meaning:
                        # 根拠が埋め込みだけで LLM も否定 → 無関係なファイルの可能性が高いので候補を出さない
                        return ClassificationResult(
                            category_id=None, confidence=0.0, source=self.name + "+llm",
                            reasons=[f"どのカテゴリにも当てはまりません（類似度 {sim:.2f}）"] + notes,
                            new_category=base.new_category)
                    # 埋め込みが迷っている場合は、候補として残す（自動移動はしない）
                    conf = min(conf, 0.4)
                elif llm_used:
                    picked_sim, picked = cands[idx]
                    if picked.category_id == cat.category_id:
                        notes.append(f"LLM も同じカテゴリを選択（{reason}）")
                        weak_meaning = False
                        conf = max(conf, 0.45) if sim < MIN_SUGGEST_SIM else conf
                    elif confirm_only:
                        # 埋め込みが確信している場合は、LLM が別の候補を選んでも埋め込みの判断を残す
                        notes.append(f"LLM は別の候補を選択（{reason}）。埋め込みの判断を表示します")
                        conf = min(conf, 0.6)
                    else:
                        notes.append(f"LLM が別の候補を選択（{reason}）")
                        cat, sim = picked, picked_sim
                        weak_meaning = False
                        # 埋め込みの 1 位と食い違うので、要確認の候補にとどめる
                        conf = min(self._confidence(sim, 0.0), 0.6)

        if weak_meaning:
            # 意味の近さがはっきりしない: 語の一致がはっきりしていればそちらを、どちらも弱ければ候補なし
            if base.category_id is not None and base.confidence >= FALLBACK_HEURISTIC_CONF:
                base.reasons.append(f"（意味の近さははっきりしません: 類似度 {sim:.2f}）")
                base.reasons.extend(notes)
                return base
            return ClassificationResult(category_id=None, confidence=0.0, source=self.name,
                                        reasons=[f"どのカテゴリとも意味の近さがはっきりしません（類似度 {sim:.2f}）"]
                                        + notes, new_category=base.new_category)
        _, matched, _ = self.heuristic.score_category(doc, cat)
        # 内容ベース分類が同じカテゴリを 1 位に選び、しかも自分でもある程度確信している場合だけ「一致」
        agreed = base.category_id == cat.category_id and base.confidence >= AGREE_MIN_CONFIDENCE
        reasons = [f"カテゴリ「{cat.category_name}」と意味が近い（類似度 {sim:.2f}、次点との差 {margin:.2f}）"] + notes
        if matched:
            reasons.append(f"一致キーワード: {', '.join(matched[:6])}")
        if not agreed:
            reasons.append("内容ベース分類の候補と一致しません")
        return ClassificationResult(
            category_id=cat.category_id, confidence=round(conf, 3), reasons=reasons, matched_keywords=matched,
            source=self.name + ("+llm" if llm_used else ""),
            scores={c.category_id: round(s_, 3) for s_, c in ranked[:5]},
            new_category=base.new_category if conf < 0.45 else None, cross_checked=agreed)

    # ---------- 分類体系の考案 ----------
    def propose_taxonomy(self, docs: list[DocInput], taxonomy: Taxonomy) -> TaxonomyDraft:
        try:
            vecs = self.embed_docs(docs)
        except OllamaError as e:
            self.last_error = str(e)
            draft = self.heuristic.propose_taxonomy(docs, taxonomy)
            draft.notes.append("埋め込みモデルが利用できないため、内容ベースで考案しました")
            return draft
        draft = TaxonomyDraft(source=self.name)
        remaining, rvecs = [], []
        for d, v in zip(docs, vecs):
            ranked = self._ranked(v, taxonomy)
            if ranked:
                sim, cat = ranked[0]
                margin = sim - (ranked[1][0] if len(ranked) > 1 else SIM_FLOOR)
                if self._confidence(sim, margin) >= REUSE_CONFIDENCE:
                    draft.reused[str(d.path)] = cat.category_id
                    continue
            remaining.append(d)
            rvecs.append(v)
        if remaining:
            used = {c.category_name.lower() for c in taxonomy.active()}
            self._build(remaining, rvecs, None, 0, draft, used)
            assigned = {m for c in draft.new_categories for m in c.members}
            draft.unassigned = [str(d.path) for d in remaining if str(d.path) not in assigned]
            if draft.unassigned:
                draft.notes.append(f"{len(draft.unassigned)} 件は他のファイルと意味のまとまりが弱く、カテゴリを作りませんでした")
        if self.namer is not None and draft.new_categories:
            draft = self.namer.name_draft(draft, docs)
            if getattr(self.namer, "last_error", ""):
                self.last_error = self.namer.last_error
        draft.source = self.name
        return draft

    def _build(self, docs: list[DocInput], vecs: list[list[float]], parent_id: str | None, depth: int,
               draft: TaxonomyDraft, used: set[str]) -> None:
        threshold = CLUSTER_SIM if depth == 0 else SUBCLUSTER_SIM
        groups = cluster_vectors(vecs, threshold)
        for g in groups:
            if len(g) < MIN_CLUSTER:
                continue
            members = [docs[i] for i in g]
            terms = rank_terms(members, docs) or [d.path.stem for d in members[:1]]
            name = unique_name(make_name(terms[:2]), used, terms)
            used.add(name.lower())
            top = terms[:8]
            cid = new_category_id()
            cat = DraftCategory(
                category_id=cid, name=name,
                description=describe(terms[:3], [m.path.name for m in members], [m.features.ext for m in members]),
                keywords=top, keyword_weights=profile_weights(members, docs, top), parent_category_id=parent_id,
                examples=[m.path.name for m in members[:5]], members=[str(m.path) for m in members],
            )
            draft.new_categories.append(cat)
            if len(g) >= SUBDIVIDE_MIN and depth == 0:
                sub = [s for s in cluster_vectors([vecs[i] for i in g], SUBCLUSTER_SIM) if len(s) >= MIN_CLUSTER]
                if len(sub) >= 2 and sum(len(s) for s in sub) >= 0.6 * len(g):
                    before = len(draft.new_categories)
                    self._build(members, [vecs[i] for i in g], cid, depth + 1, draft, used)
                    child = {m for c in draft.new_categories[before:] for m in c.members}
                    cat.members = [m for m in cat.members if m not in child]


def cluster_vectors(vecs: list[list[float]], threshold: float) -> list[list[int]]:
    """平均連結の凝集型クラスタリング（決定的）。返り値は添字のグループ（大きい順）。"""
    n = len(vecs)
    if n == 0:
        return []
    idx = list(range(min(n, MAX_CLUSTER_DOCS)))
    groups = {i: [i] for i in idx}
    sim = {i: {j: dot(vecs[i], vecs[j]) for j in idx if j != i} for i in idx}
    while True:
        best = None
        for a in groups:
            for b, s in sim[a].items():
                if a < b and (best is None or s > best[0]):
                    best = (s, a, b)
        if best is None or best[0] < threshold:
            break
        _, a, b = best
        na, nb = len(groups[a]), len(groups[b])
        for k in list(sim[a]):
            if k == b:
                continue
            new = (na * sim[a][k] + nb * sim[b][k]) / (na + nb)
            sim[a][k] = new
            sim[k][a] = new
        for k in list(sim[b]):
            sim[k].pop(b, None)
        sim[a].pop(b, None)
        del sim[b]
        groups[a].extend(groups.pop(b))
    out = [sorted(g) for g in groups.values()]
    # 上限を超えた分は、最も近いまとまりに割り当てる
    if n > MAX_CLUSTER_DOCS:
        cents = [mean([vecs[i] for i in g]) for g in out]
        for i in range(MAX_CLUSTER_DOCS, n):
            scores = [dot(vecs[i], c) for c in cents]
            k = max(range(len(scores)), key=lambda x: scores[x])
            if scores[k] >= threshold:
                out[k].append(i)
            else:
                out.append([i])
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


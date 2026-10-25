"""埋め込み（意味の近さ）による分類のテスト。Ollama の代わりに、話題ごとの決まったベクトルを返す偽物を使う。"""
import math

from classifier.ollama import OllamaError
from classifier.semantic import SemanticClassifier, cluster_vectors
from conftest import write
from history.db import F_MOVED, F_REVIEW

# 話題ごとの軸。文書に含まれる語でベクトルを作る（語が違っても同じ話題なら近くなる）
TOPICS = {
    "garden": ["トマト", "種まき", "コンポスト", "肥料", "菜園", "水やり"],
    "money": ["請求書", "領収書", "家賃", "振込", "経費", "金額"],
    "trip": ["京都", "旅館", "新幹線", "観光", "嵐山"],
}


class FakeEmbedClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def embed(self, texts, model):
        self.calls += 1
        if self.fail:
            raise OllamaError("connection refused")
        out = []
        for t in texts:
            v = [0.15, 0.15, 0.15, 0.15]          # どの文書にも少しずつある共通成分
            for k, (topic, words) in enumerate(TOPICS.items()):
                v[k] += sum(1.0 for w in words if w in t)
            n = math.sqrt(sum(x * x for x in v))
            out.append([x / n for x in v])
        return out


FILES = {
    "tomato_plan.txt": "トマトの種まき計画 菜園",
    "garden_log.md": "菜園の水やり記録 トマト",
    "hiryou.csv": "肥料 菜園 種まき",
    "invoice_04.txt": "請求書 金額 振込",
    "receipt.md": "領収書 経費 金額",
    "rent.csv": "家賃 振込 請求書",
}


def _engine(make_engine, dirs, fail=False, **kw):
    for n, t in FILES.items():
        write(dirs["watch"] / n, t)
    clf = SemanticClassifier(FakeEmbedClient(fail), "fake-embed")
    e = make_engine(**kw)
    e._classifier_override = clf
    clf.bind_store(e.vectors)
    return e, clf


def _approve(e):
    ok, msg = e.approve_proposals([p.id for p in e.db.list_proposals("pending")])
    assert ok, msg


def _cat_of(e, filename):
    for p in e.db.list_proposals(None):
        if any(f.endswith(filename) for f in p.related_files):
            return p.operations[0]["category"]["category_id"]
    raise AssertionError(filename)


def test_cluster_vectors_groups_similar():
    a, b = [1.0, 0.0], [0.0, 1.0]
    groups = cluster_vectors([a, a, b, b, [0.99, 0.14]], 0.8)
    assert sorted(len(g) for g in groups) == [2, 3]


def test_propose_groups_by_meaning(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    summary = e.propose_taxonomy()
    assert summary["source"] == "semantic"
    groups = [sorted(p.related_files) for p in e.db.list_proposals("pending")]
    assert len(groups) == 2
    assert _cat_of(e, "tomato_plan.txt") == _cat_of(e, "hiryou.csv") != _cat_of(e, "receipt.md")


def test_classifies_without_shared_words(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    e.propose_taxonomy()
    _approve(e)
    garden = _cat_of(e, "tomato_plan.txt")
    # 「コンポスト」は seed のどのファイルにも出てこないが、意味（話題）は家庭菜園
    f = write(dirs["watch"] / "untitled.html", "コンポスト 水やり")
    out = e.process_file(f, "event")
    assert out.category_id == garden


def test_unrelated_file_gets_no_candidate(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    e.propose_taxonomy()
    _approve(e)
    f = write(dirs["watch"] / "cat_diary.txt", "猫の爪切り")
    out = e.process_file(f, "event")
    assert out.status == F_REVIEW and out.category_id is None


def test_learns_from_moves_and_forgets_on_undo(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    e.propose_taxonomy()
    _approve(e)
    garden = _cat_of(e, "tomato_plan.txt")
    before = e.vectors.count(garden, "fake-embed")
    f = write(dirs["watch"] / "untitled.html", "コンポスト 水やり")
    mv = e.move_manually(str(f), garden)
    assert mv.ok
    assert e.vectors.count(garden, "fake-embed") == before + 1
    e.undo(e.db.list_operations()[0].id)
    assert e.vectors.count(garden, "fake-embed") == before


def test_merge_moves_features_to_target(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    e.propose_taxonomy()
    _approve(e)
    garden, money = _cat_of(e, "tomato_plan.txt"), _cat_of(e, "receipt.md")
    n = e.vectors.count(garden, "fake-embed") + e.vectors.count(money, "fake-embed")
    ok, _ = e.change_taxonomy([{"op": "merge", "source_ids": [money], "target_id": garden}], "統合テスト")
    assert ok and e.vectors.count(garden, "fake-embed") == n and e.vectors.count(money, "fake-embed") == 0


def test_auto_move_requires_agreement_with_content(make_engine, dirs):
    e, clf = _engine(make_engine, dirs, preview_mode=False, auto_move=True)
    e.propose_taxonomy()
    _approve(e)
    # 意味は近いが語の一致がない → 自動では動かさない
    f = write(dirs["watch"] / "untitled.html", "コンポスト 水やり")
    assert e.process_file(f, "event").status == F_REVIEW and f.exists()
    # 意味も語も一致 → 自動移動できる
    g = write(dirs["watch"] / "tomato_harvest.txt", "トマト 菜園 種まき 肥料 水やり")
    assert e.process_file(g, "event").status == F_MOVED


def test_falls_back_when_embedding_unavailable(make_engine, dirs):
    e, clf = _engine(make_engine, dirs, fail=True)
    summary = e.propose_taxonomy()
    assert any("内容ベース" in n for n in summary["notes"])


def test_embedding_cache_avoids_recompute(make_engine, dirs):
    e, clf = _engine(make_engine, dirs)
    e.propose_taxonomy()
    calls = clf.client.calls
    e.propose_taxonomy()     # 同じファイル（サイズ・更新日時が同じ）は再計算しない
    assert clf.client.calls == calls


class FakeChooser:
    """LLM の代わり。決まった番号を返し、呼ばれた回数を数える。"""

    def __init__(self, pick_name=None):
        self.pick_name = pick_name      # 選ばせたいカテゴリ名に含まれる語（None なら該当なし）
        self.calls = 0
        self.last_error = ""

    def pick(self, doc, cats):
        self.calls += 1
        if self.pick_name is None:
            return None, 0.9, "どれにも当てはまらない"
        for i, c in enumerate(cats):
            if any(self.pick_name in e for e in c.examples):
                return i, 0.9, "内容が近い"
        return None, 0.5, "見つからない"

    def name_draft(self, draft, docs):
        return draft


def _engine_with_chooser(make_engine, dirs, chooser, **kw):
    e, clf = _engine(make_engine, dirs, **kw)
    clf.namer = chooser
    return e, clf


def test_llm_not_called_when_embedding_is_confident(make_engine, dirs):
    chooser = FakeChooser("invoice")
    e, clf = _engine_with_chooser(make_engine, dirs, chooser)
    e.propose_taxonomy()
    _approve(e)
    chooser.calls = 0
    f = write(dirs["watch"] / "tomato_harvest.txt", "トマト 菜園 種まき 肥料 水やり")
    out = e.process_file(f, "event")
    assert chooser.calls == 0 and out.category_id == _cat_of(e, "tomato_plan.txt")


def test_llm_choice_among_candidates_goes_to_review(make_engine, dirs):
    chooser = FakeChooser("invoice")      # 経理のカテゴリを選ぶ LLM
    e, clf = _engine_with_chooser(make_engine, dirs, chooser, preview_mode=False, auto_move=True)
    e.propose_taxonomy()
    _approve(e)
    # 菜園と経理の語が半々: 埋め込みは迷う → LLM に聞き、LLM の選んだ経理を候補にするが自動移動はしない
    f = write(dirs["watch"] / "mixed.txt", "トマト 菜園 請求書 金額")
    out = e.process_file(f, "event")
    assert chooser.calls == 1
    assert out.category_id == _cat_of(e, "receipt.md") and out.status == F_REVIEW and f.exists()


def test_llm_none_keeps_candidate_with_low_confidence(make_engine, dirs):
    chooser = FakeChooser(None)
    e, clf = _engine_with_chooser(make_engine, dirs, chooser)
    e.propose_taxonomy()
    _approve(e)
    # 埋め込みが迷うファイルで LLM が「該当なし」→ 候補は残すが信頼度は低く、要確認
    f = write(dirs["watch"] / "mixed.txt", "トマト 菜園 請求書 金額")
    out = e.process_file(f, "event")
    assert chooser.calls == 1
    assert out.status == F_REVIEW and (out.category_id is None or out.confidence <= 0.4)


def test_embedding_only_candidate_dropped_when_llm_says_none(make_engine, dirs):
    chooser = FakeChooser(None)
    e, clf = _engine_with_chooser(make_engine, dirs, chooser)
    e.propose_taxonomy()
    _approve(e)
    chooser.calls = 0
    # 埋め込みは菜園に近いと判断するが、語の一致はない → LLM に確認し、該当なしなら候補を出さない
    f = write(dirs["watch"] / "untitled.html", "コンポスト")
    out = e.process_file(f, "event")
    assert chooser.calls == 1
    assert out.status == F_REVIEW and out.category_id is None


def test_llm_consulted_when_content_disagrees_even_if_embedding_confident(make_engine, dirs):
    chooser = FakeChooser("tomato")
    e, clf = _engine_with_chooser(make_engine, dirs, chooser)
    e.propose_taxonomy()
    _approve(e)
    chooser.calls = 0
    f = write(dirs["watch"] / "untitled.html", "コンポスト")
    out = e.process_file(f, "event")
    assert chooser.calls == 1 and out.category_id == _cat_of(e, "tomato_plan.txt")

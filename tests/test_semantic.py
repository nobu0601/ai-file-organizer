"""意味ベクトルによるまとめ方（平均連結）の検証。"""
import random

from classifier.semantic import average_linkage


def test_average_linkage_groups_close_vectors():
    vecs = [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.1, 0.95, 0], [0, 0, 1]]
    assert average_linkage(vecs, 0.8) == [[0, 1], [2, 3], [4]]


def test_average_linkage_ignores_common_component():
    # 全ベクトルに共通成分があっても、テーマ間の差で分かれる（中心ベクトル方式では 1 つにまとまる例）
    base = [0.6] * 4
    vecs = [[b + d for b, d in zip(base, delta)] for delta in
            ([1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 0, 1, 0], [0, 0, 0.9, 0.1])]
    groups = average_linkage(vecs, 0.85)
    assert sorted(groups) == [[0, 1], [2, 3]]


def test_average_linkage_is_order_independent():
    rnd = random.Random(1)
    vecs = [[rnd.random() for _ in range(6)] for _ in range(12)]
    expected = sorted(sorted(g) for g in average_linkage(vecs, 0.8))
    order = list(range(12))
    rnd.shuffle(order)
    shuffled = average_linkage([vecs[i] for i in order], 0.8)
    assert sorted(sorted(order[i] for i in g) for g in shuffled) == expected


def test_average_linkage_edge_cases():
    assert average_linkage([], 0.5) == []
    assert average_linkage([[1, 0]], 0.5) == [[0]]
    assert average_linkage([[0, 0], [1, 0]], 0.5) == [[0], [1]]   # ゼロベクトルでも落ちない

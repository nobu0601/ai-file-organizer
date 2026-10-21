"""意味の近さ（埋め込みベクトル）によるファイルのまとめ方。

単語の一致だけでは「請求書」「領収書」「見積書」のように語が重ならない同テーマのファイルを
まとめられないため、Ollama の埋め込みモデルが使える場合は文章の意味ベクトルでまとめる。
しきい値は EMBED_MODEL の類似度の分布に合わせた値なので、モデルを変える場合は測り直すこと
（scripts/eval_accuracy.py で確認できる）。
"""
from __future__ import annotations

import math

EMBED_MODEL = "qwen3-embedding:0.6b"
EMBED_SIZE_GB = 0.6
# 平均連結でこれ以上似ているグループ同士をまとめる。
# 評価データでは 0.36〜0.40 の範囲でテーマの分かれ方がほぼ同じだったため、その中央を使う
SEMANTIC_THRESHOLD = 0.38
# 平均連結は件数の 3 乗に比例して遅くなるため、これを超える場合は単語ベースでまとめる
MAX_SEMANTIC_DOCS = 600
EMBED_TEXT_CHARS = 1500


def embed_text(name_stem: str, text: str) -> str:
    return f"ファイル名: {name_stem}\n{(text or '')[:EMBED_TEXT_CHARS]}"


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else [0.0] * len(v)


def average_linkage(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """平均連結の凝集型クラスタリング。結果は入力順に依存しない（決定的）。

    中心ベクトル方式は、埋め込みベクトルに共通成分があるため全件が 1 つにまとまりやすい。
    平均連結はグループ間の全ペアの類似度の平均で判断するので、その影響を受けにくい。
    """
    n = len(vectors)
    if n == 0:
        return []
    unit = [_unit(v) for v in vectors]
    sim = [[sum(a * b for a, b in zip(unit[i], unit[j])) for j in range(n)] for i in range(n)]
    groups: dict[int, list[int]] = {i: [i] for i in range(n)}
    # total[a][b] = グループ a と b の間の類似度の合計
    total: dict[int, dict[int, float]] = {i: {j: sim[i][j] for j in range(n) if j != i} for i in range(n)}
    while len(groups) > 1:
        best = None
        for a in groups:
            la = len(groups[a])
            for b, s in total[a].items():
                if b <= a:
                    continue
                avg = s / (la * len(groups[b]))
                if best is None or avg > best[0] + 1e-12:
                    best = (avg, a, b)
        if best is None or best[0] < threshold:
            break
        _, a, b = best
        groups[a].extend(groups.pop(b))
        tb = total.pop(b)
        del total[a][b]
        for c, s in tb.items():
            if c == a:
                continue
            total[a][c] += s
            total[c][a] += s
            del total[c][b]
    return [sorted(g) for g in sorted(groups.values(), key=lambda g: (-len(g), min(g)))]

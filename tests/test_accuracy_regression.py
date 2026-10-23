"""内容ベース分類の精度が下がっていないことを、正解付き評価データで確認する。

評価データ A は調整に使ったデータ、B は調整に使っていない検証用データ。
下限値は改善時の実測（A: 53%、B: 39%）から少し余裕を持たせている。
誤移動（自動移動の誤り・無関係ファイルの移動）は常に 0 件でなければならない。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_accuracy  # noqa: E402
from classifier.heuristic import HeuristicClassifier  # noqa: E402


@pytest.mark.parametrize("name, min_acc, min_themes", [("dataset.json", 0.45, 7), ("dataset_b.json", 0.33, 5)])
def test_heuristic_accuracy(name, min_acc, min_themes):
    ds = json.loads((ROOT / "scripts" / "eval" / name).read_text(encoding="utf-8"))
    r = eval_accuracy.run(ds, "heuristic", HeuristicClassifier(), 0.85)
    assert r["holdout_acc"] >= min_acc, r["holdout_acc"]
    assert r["themes_covered"] >= min_themes
    assert r["distractor_moved"] == 0
    assert r["auto_moved"] == r["auto_moved_correct"]

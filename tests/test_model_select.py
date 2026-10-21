from classifier.model_select import choose_installed, recommended


def test_recommended_by_memory():
    assert recommended(32).name == "qwen3:8b"
    assert recommended(16).name == "qwen3:8b"
    assert recommended(8).name == "qwen3:4b"
    assert recommended(4).name == "qwen2.5:1.5b"
    assert recommended(None).name == "qwen3:4b"      # 不明なら 8GB 機とみなす


def test_choose_installed_prefers_best_that_fits():
    installed = ["qwen2.5:3b", "qwen3:8b", "llama3:latest"]
    assert choose_installed(installed, 16) == "qwen3:8b"
    assert choose_installed(installed, 8) == "qwen2.5:3b"   # 8b は 8GB 機では選ばない
    assert choose_installed(["llama3"], 16) is None

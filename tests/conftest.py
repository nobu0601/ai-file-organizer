"""テスト共通設定。すべて pytest の一時ディレクトリ内で完結させ、実ユーザーデータには触れない。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from organizer.engine import Engine
from settings.config import Settings, WatchFolder
from settings.paths import AppPaths


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    # 万一 AppPaths() が引数なしで呼ばれても一時ディレクトリを使う
    monkeypatch.setenv("AIFO_DATA_DIR", str(tmp_path / "default-appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture
def dirs(tmp_path):
    d = {
        "data": tmp_path / "appdata",
        "watch": tmp_path / "Downloads",
        "dest": tmp_path / "Organized",
    }
    for p in d.values():
        p.mkdir()
    return d


@pytest.fixture
def make_engine(dirs):
    engines = []

    def factory(**overrides) -> Engine:
        s = Settings(organize_root=str(dirs["dest"]), organize_in_place=overrides.pop("in_place", False),
                     watch_folders=[WatchFolder(str(dirs["watch"]), overrides.pop("recursive", False))],
                     stability_checks=overrides.pop("stability_checks", 1),
                     stability_quiet_seconds=overrides.pop("quiet", 0.0),
                     stability_poll_seconds=0.05)
        for k, v in overrides.items():
            setattr(s, k, v)
        e = Engine(AppPaths(dirs["data"]), s)
        engines.append(e)
        return e

    yield factory
    for e in engines:
        try:
            e.shutdown()
        except Exception:
            pass


def write(path: Path, text: str, mtime_offset: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime_offset:
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + mtime_offset))
    return path


SAMPLE_FILES = {
    "alpha_設計書_v2.txt": "Alphaプロジェクト 設計書。モーター制御基板の仕様と試験計画を記載。",
    "alpha_試験結果.csv": "項目,結果\nAlpha モーター 耐久試験,合格\nAlpha 温度試験,合格",
    "alpha_解析.md": "# Alpha 解析\nモーター電流の解析結果。Alpha基板の温度特性。",
    "請求書_2026_04_山田商事.txt": "請求書 山田商事 御中 金額 120,000円 支払期限 2026年5月末 振込先",
    "請求書_2026_05_山田商事.txt": "請求書 山田商事 御中 金額 80,000円 支払期限 2026年6月末 振込先",
    "travel_kyoto_plan.md": "Kyoto trip itinerary: temple visit, hotel booking, train schedule",
    "kyoto_hotel_booking.txt": "Hotel booking confirmation Kyoto check-in itinerary",
}


@pytest.fixture
def sample_files(dirs):
    return [write(dirs["watch"] / n, t) for n, t in SAMPLE_FILES.items()]


def approve_all(engine: Engine) -> None:
    ids = [p.id for p in engine.db.list_proposals("pending")]
    ok, msg = engine.approve_proposals(ids)
    assert ok, msg


def category_by_keyword(engine: Engine, kw: str):
    for c in engine.store.taxonomy.active():
        if kw in c.keywords:
            return c
    raise AssertionError(f"keyword {kw} not found in {[c.keywords for c in engine.store.taxonomy.active()]}")

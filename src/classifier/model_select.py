"""PC のメモリに合わせてローカル LLM を自動で選ぶ。

GPU のないノート PC を前提に、CPU で実用的な速さで動く範囲（〜8B）から
性能の高い順に候補を並べている。メモリには OS や他のアプリの分の余裕を見込む。
"""
from __future__ import annotations

from dataclasses import dataclass

AUTO = "auto"


@dataclass(frozen=True)
class Candidate:
    name: str
    min_ram_gb: float     # これ未満の PC では選ばない
    size_gb: float        # ダウンロードサイズの目安


# 性能の高い順
CANDIDATES = [
    Candidate("qwen3:8b", 15.0, 5.2),
    Candidate("qwen2.5:7b", 15.0, 4.7),
    Candidate("qwen3:4b", 7.5, 2.6),
    Candidate("qwen2.5:3b", 7.5, 1.9),
    Candidate("qwen2.5:1.5b", 3.5, 1.0),
]


def normalize(name: str) -> str:
    name = name.strip()
    return name if ":" in name else name + ":latest"


def fits(c: Candidate, ram_gb: float | None) -> bool:
    # メモリが測れないときは 8GB 機とみなす（Surface の最小構成）
    return c.min_ram_gb <= (ram_gb if ram_gb else 8.0)


def recommended(ram_gb: float | None) -> Candidate:
    for c in CANDIDATES:
        if fits(c, ram_gb):
            return c
    return CANDIDATES[-1]


def choose_installed(installed: list[str], ram_gb: float | None) -> str | None:
    """インストール済みのモデルから、この PC で動く最も性能の高い候補を選ぶ。"""
    have = {normalize(n) for n in installed}
    for c in CANDIDATES:
        if fits(c, ram_gb) and normalize(c.name) in have:
            return c.name
    return None


def is_installed(model: str, installed: list[str]) -> bool:
    return normalize(model) in {normalize(n) for n in installed}

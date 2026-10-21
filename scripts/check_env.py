"""AI File Organizer の実行環境を確認するスクリプト。

標準ライブラリのみを使うため、依存パッケージのインストール前に実行できる。
何もインストールや変更は行わず、結果を表示するだけ。
"""

import ctypes
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
GB = 1024 ** 3


class MEMORYSTATUSEX(ctypes.Structure):
    """Windows の GlobalMemoryStatusEx 用構造体。"""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def check_python():
    v = sys.version_info
    print(f"Python バージョン: {v.major}.{v.minor}.{v.micro}")
    print(f"Python 実行ファイル: {sys.executable}")
    if v < (3, 10):
        print("[警告] Python 3.10 以上が必要です。python.org から新しい Python をインストールしてください。")


def check_tkinter():
    try:
        import tkinter  # noqa: F401
        print("tkinter: 利用できます")
    except ImportError:
        print("tkinter: 利用できません")
        print("[案内] python.org の Python インストーラーで「tcl/tk and IDLE」を選択して再インストールしてください。")


def get_total_memory_gb():
    """物理メモリの合計（GB）を返す。取得できなければ None。"""
    if os.name == "nt":
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return stat.ullTotalPhys / GB
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GB
    except (AttributeError, ValueError, OSError):
        return None


def check_memory():
    mem = get_total_memory_gb()
    if mem is None:
        print("物理メモリ: 不明")
        return
    print(f"物理メモリ: {mem:.1f} GB")
    # 判定の境目はアプリ本体（src/classifier/model_select.py）に合わせる
    if mem < 3.5:
        print("推奨: Ollama なしの内容ベース分類を推奨")
    elif mem < 7.5:
        print("推奨: qwen2.5:1.5b（約 1GB）")
    elif mem < 15:
        print("推奨: qwen3:4b（約 2.6GB）")
    else:
        print("推奨: qwen3:8b（約 5.2GB。GPU なしでは 8B 程度が実用上の上限）")


def check_disk():
    home = Path.home()
    free_gb = shutil.disk_usage(home).free / GB
    print(f"ホームドライブの空き容量: {free_gb:.1f} GB（{home}）")
    if free_gb < 10:
        print("[警告] 空き容量が 10 GB 未満です。Ollama のモデルは数 GB の容量が必要です。")


def check_ollama():
    # プロキシを経由させず、ローカルの Ollama に直接問い合わせる
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(OLLAMA_TAGS_URL, timeout=2) as res:
            data = json.loads(res.read().decode("utf-8"))
    except (OSError, ValueError):
        print("Ollama: 応答なし（未起動、または未インストール。Ollama は任意です）")
        return
    models = [m.get("name", "?") for m in data.get("models", [])]
    print("Ollama: 応答あり")
    if models:
        print("インストール済みモデル: " + ", ".join(models))
    else:
        print("インストール済みモデル: なし")


CHECKS = [
    ("Python", check_python),
    ("tkinter", check_tkinter),
    ("メモリ", check_memory),
    ("ディスク", check_disk),
    ("Ollama", check_ollama),
]


def main():
    print("=== AI File Organizer 環境チェック ===")
    for title, func in CHECKS:
        print(f"\n[{title}]")
        try:
            func()
        except Exception as e:  # 1つの確認が失敗しても他の確認は続ける
            print(f"[エラー] {title} の確認に失敗しました: {e}")
    print("\n確認が終わりました。何もインストール・変更はしていません。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

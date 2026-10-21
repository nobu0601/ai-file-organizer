"""AI File Organizer のエントリーポイント。

    python src/main.py              GUI を起動
    python src/main.py --minimized  最小化して起動（自動起動用）
    python src/main.py --data-dir D データ保存先を変更（検証用）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings.paths import AppPaths  # noqa: E402


def setup_logging(paths: AppPaths) -> None:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(paths.log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI File Organizer")
    ap.add_argument("--data-dir", help="アプリデータの保存先（既定: %%LOCALAPPDATA%%\\AIFileOrganizer）")
    ap.add_argument("--minimized", action="store_true")
    ap.add_argument("--start-monitoring", action="store_true", help="起動時に監視を開始する")
    args = ap.parse_args(argv)
    if args.data_dir:
        os.environ["AIFO_DATA_DIR"] = args.data_dir
    paths = AppPaths().ensure()
    setup_logging(paths)
    log = logging.getLogger("main")
    log.info("起動: data=%s", paths.root)

    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter が見つかりません。python.org 版の Python（tcl/tk を含む）をインストールしてください。")
        return 1
    from gui.app import App
    from organizer.engine import Engine

    engine = Engine(paths)
    root = tk.Tk()
    if args.minimized:
        root.iconify()
    app = App(engine, root)
    if args.start_monitoring and engine.settings.watch_folders:
        engine.start_monitoring()
        app.ui.run(lambda: engine.scan_now("startup"))
    app.run()
    log.info("終了")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import time

from conftest import approve_all, write
from history.db import F_READY
from watcher.queue import Q_FAILED, Q_PROCESSING, Q_WAITING, ProcessingQueue


def wait_until(cond, timeout=10.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def test_watchdog_detects_add_and_update(make_engine, dirs):
    e = make_engine()
    e.monitor.start([(str(dirs["watch"]), False)])
    e.paused = True   # キューに入るところまでを確認する
    try:
        f = write(dirs["watch"] / "new_report.txt", "first")
        assert wait_until(lambda: any(i.path == str(f) for i in e.queue.snapshot()))
        write(f, "updated content")
        time.sleep(0.3)
        items = [i for i in e.queue.snapshot() if i.path == str(f)]
        assert len(items) == 1   # 追加と更新のイベントが 1 件に集約される
    finally:
        e.monitor.stop()


def test_duplicate_events_coalesced(tmp_path):
    q = ProcessingQueue(checks=1, quiet_seconds=0)
    f = write(tmp_path / "a.txt", "x")
    assert q.enqueue(f, "event") is True
    assert q.enqueue(f, "event") is False
    assert q.enqueue(f, "event") is False
    assert len(q.snapshot()) == 1


def test_writing_file_is_deferred_until_stable(tmp_path):
    q = ProcessingQueue(checks=3, quiet_seconds=2.0)
    f = write(tmp_path / "dl.zip.txt", "x")
    now = time.time()
    q.enqueue(f, "event", now=now)
    # サイズが変わり続ける間は処理しない
    for i in range(4):
        write(f, "x" * (i + 2))
        assert q.poll_stability(now=now + i) == []
    # 3 回安定しても最後の変更から 2 秒未満なら処理しない
    t = time.time()
    assert q.poll_stability(now=t) == []
    assert q.poll_stability(now=t + 0.5) == []
    assert q.poll_stability(now=t + 1.0) == []
    ready = q.poll_stability(now=t + 6.0)
    assert [i.path for i in ready] == [str(f)] and ready[0].status == Q_PROCESSING


def test_locked_file_is_retried_then_fails(tmp_path):
    q = ProcessingQueue(checks=1, quiet_seconds=0, max_attempts=3, open_check=lambda p: False)
    f = write(tmp_path / "locked.txt", "x")
    q.enqueue(f, "event", now=time.time() - 10)
    for _ in range(3):
        assert q.poll_stability(now=time.time() + 10) == []
    assert q.snapshot()[0].status == Q_FAILED


def test_update_during_processing_rechecks(tmp_path):
    q = ProcessingQueue(checks=1, quiet_seconds=0)
    f = write(tmp_path / "a.txt", "x")
    q.enqueue(f, "event", now=time.time() - 10)
    assert q.poll_stability(now=time.time() + 10)
    q.enqueue(f, "event")      # 処理中の更新イベント
    q.finish(str(f), "完了", "done")
    assert q.snapshot()[0].status == Q_WAITING


def test_files_added_while_stopped_are_found_on_startup(make_engine, sample_files, dirs):
    e = make_engine()
    e.propose_taxonomy()
    approve_all(e)
    # 承認時に関連ファイルは再分類済み。スキャンしても処理済みは再投入しない
    assert e.scan_now() == 0
    assert all(e.db.get_file(f).status == F_READY for f in sample_files)
    e.shutdown()
    # アプリ停止中に追加
    f = write(dirs["watch"] / "alpha_試験結果_停止中.csv", "Alpha モーター 試験 結果 温度 基板")
    e2 = make_engine()
    assert e2.scan_now("startup") == 1     # 処理済みのファイルは再処理しない
    item = e2.queue.snapshot()[0]
    assert item.path == str(f) and item.origin == "startup"
    e2.process_queue_once()
    assert e2.db.get_file(f).status == F_READY


def test_organize_root_inside_watch_folder_no_loop(make_engine, dirs, sample_files):
    # 整理先を監視フォルダの中に置き、再帰監視しても再分類ループにならない
    e = make_engine(recursive=True, organize_root=str(dirs["watch"] / "整理済み"), preview_mode=False, auto_move=True)
    e.rebuild()
    e.propose_taxonomy()
    approve_all(e)
    e.scan_now()
    e.process_queue_once()
    e.execute_ready()
    moved = list((dirs["watch"] / "整理済み").rglob("*.*"))
    assert moved
    for m in moved:
        assert e.should_track(str(m)) is False
    assert e.scan_now() == 0


def test_watch_folder_validation(make_engine, dirs):
    e = make_engine()
    import pytest
    with pytest.raises(ValueError):
        e.add_watch_folder(str(dirs["dest"]))
    with pytest.raises(ValueError):
        e.add_watch_folder(str(dirs["data"]))
    with pytest.raises(ValueError):
        e.add_watch_folder(str(dirs["watch"]))   # 重複


def test_end_to_end_monitoring_auto_move(make_engine, sample_files, dirs):
    e = make_engine(preview_mode=False, auto_move=True, stability_checks=2, quiet=0.2)
    e.propose_taxonomy()
    approve_all(e)
    e.scan_now()
    e.process_queue_once()
    e.start_monitoring()
    try:
        f = write(dirs["watch"] / "alpha_試験結果_監視.csv", "Alpha モーター 試験 結果 温度 基板")
        assert wait_until(lambda: not f.exists(), timeout=15)
        assert any(o.src_path == str(f) and o.result == "moved" for o in e.db.list_operations())
    finally:
        e.stop_monitoring()

import os
from pathlib import Path

from conftest import approve_all, category_by_keyword, write
from history.db import F_MOVED, F_READY, F_REVIEW, F_SKIPPED
from organizer.mover import move_no_overwrite, unique_destination


def _setup_approved(make_engine, **kw):
    e = make_engine(**kw)
    e.propose_taxonomy()
    approve_all(e)
    return e


def test_initial_files_wait_for_preview_then_move(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    e.scan_now()
    e.process_queue_once()
    ready = e.db.list_files((F_READY,))
    # 既存ファイル（初回整理）は自動移動しない
    assert ready and all(Path(r.path).exists() for r in ready)
    outs = e.execute_ready()
    assert outs and all(o.ok for o in outs)
    alpha = category_by_keyword(e, "alpha")
    moved = list((dirs["dest"] / alpha.relative_path).iterdir())
    assert {p.suffix for p in moved} >= {".txt", ".csv", ".md"}


def test_auto_move_for_new_file_when_enabled(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    out = e.process_file(f, "event")
    assert out.status == F_MOVED, out.message
    assert not f.exists() and Path(out.dest).exists()
    op = e.db.list_operations()[0]
    assert op.result == "moved" and op.src_path == str(f) and op.sha256 and op.size == Path(out.dest).stat().st_size
    assert op.category_id == out.category_id and op.reason


def test_no_move_when_auto_move_disabled_or_preview(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine)
    for kw in ({"preview_mode": False, "auto_move": False}, {"preview_mode": True, "auto_move": True}):
        for k, v in kw.items():
            setattr(e.settings, k, v)
        f = write(dirs["watch"] / "alpha_試験結果_x.csv", "Alpha モーター 試験 結果 温度 基板")
        out = e.process_file(f, "event")
        assert out.status == F_READY and f.exists()
        e.db.delete_file(f)
        f.unlink()


def test_review_files_never_moved(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    g = write(dirs["watch"] / "猫の飼育メモ.txt", "猫 キャットフード 動物病院")
    out = e.process_file(g, "event")
    assert out.status == F_REVIEW and g.exists()
    # 機密っぽいファイルも同じく要確認
    s = write(dirs["watch"] / "alpha_試験メモ.txt", "Alpha モーター 試験 結果 password=hunter2")
    out = e.process_file(s, "event")
    assert out.status == F_REVIEW and "機密" in out.message and s.exists()
    # 実行待ちの一括実行でも要確認は対象外
    e.execute_ready()
    assert g.exists() and s.exists()


def test_name_collision_does_not_overwrite(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    alpha = category_by_keyword(e, "alpha")
    target_dir = dirs["dest"] / alpha.relative_path
    existing = write(target_dir / "alpha_試験結果_2.csv", "既存の別ファイル")
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    out = e.process_file(f, "event")
    assert out.status == F_MOVED
    assert existing.read_text(encoding="utf-8") == "既存の別ファイル"
    assert Path(out.dest).name == "alpha_試験結果_2 (1).csv"


def test_move_no_overwrite_primitive(tmp_path):
    a = write(tmp_path / "a.txt", "A")
    b = write(tmp_path / "b.txt", "B")
    try:
        move_no_overwrite(a, b)
        raise AssertionError("should fail")
    except FileExistsError:
        pass
    assert a.read_text() == "A" and b.read_text() == "B"
    assert unique_destination(tmp_path, "b.txt").name == "b (1).txt"


def test_undo_and_conflicts(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    out = e.process_file(f, "event")
    op_id = e.db.list_operations()[0].id
    # 元の場所に別ファイルがある → 取り消さない
    write(f, "別のファイル")
    res = e.undo(op_id)
    assert not res.ok and "同名" in res.message and Path(out.dest).exists()
    f.unlink()
    # 正常な取り消し
    res = e.undo(op_id)
    assert res.ok and f.exists() and not Path(out.dest).exists()
    assert e.db.get_operation(op_id).result == "undone"
    # 取り消したファイルは監視で再移動しない
    again = e.process_file(f, "event")
    assert again.status == F_SKIPPED and f.exists()


def test_undo_refused_after_modification(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    out = e.process_file(f, "event")
    op_id = e.db.list_operations()[0].id
    Path(out.dest).write_text("Alpha モーター 試験 結果 温度 基板 編集済", encoding="utf-8")
    res = e.undo(op_id)
    assert not res.ok and "変更" in res.message
    assert not f.exists()
    assert e.db.get_operation(op_id).undo_error


def test_double_processing_prevented(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine, preview_mode=False, auto_move=True)
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    e._in_progress.add(os.path.normcase(os.path.abspath(f)))
    out = e.process_file(f, "event")
    assert "二重処理" in out.message and f.exists()
    e._in_progress.clear()
    first = e.process_file(f, "scan")
    assert first.status == F_READY
    second = e.process_file(f, "scan")
    assert "処理済み" in second.message
    assert len(e.db.list_operations()) == 0


def test_moved_file_changed_before_execution_is_not_moved(make_engine, sample_files, dirs):
    e = _setup_approved(make_engine)
    f = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    assert e.process_file(f, "scan").status == F_READY
    write(f, "Alpha モーター 試験 結果 温度 基板 追記", mtime_offset=5)
    outs = e.execute_ready([str(f)])
    assert len(outs) == 1 and not outs[0].ok and "変更" in outs[0].message
    assert f.exists()


def test_errors_do_not_stop_processing(make_engine, sample_files, dirs, monkeypatch):
    e = _setup_approved(make_engine)
    bad = write(dirs["watch"] / "alpha_壊れ.txt", "Alpha モーター")
    good = write(dirs["watch"] / "alpha_試験結果_2.csv", "Alpha モーター 試験 結果 温度 基板")
    real = e.analyze

    def flaky(path, root):
        if Path(path) == bad:
            raise PermissionError(13, "Access is denied")
        return real(path, root)

    monkeypatch.setattr(e, "analyze", flaky)
    e.queue.enqueue(bad, "scan", str(dirs["watch"]))
    e.queue.enqueue(good, "scan", str(dirs["watch"]))
    outs = {Path(o.path).name: o for o in e.process_queue_once()}
    assert outs[bad.name].status == "failed" and "Access is denied" in outs[bad.name].message
    assert outs[good.name].status == F_READY

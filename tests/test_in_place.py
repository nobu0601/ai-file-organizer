"""整理するフォルダの中にフォルダを作る設定（既定）の動作。"""
from conftest import approve_all, category_by_keyword, write


def test_folders_created_inside_watch_folder(make_engine, sample_files, dirs):
    e = make_engine(in_place=True)
    e.propose_taxonomy()
    approve_all(e)
    for c in e.store.taxonomy.active():
        assert (dirs["watch"] / c.relative_path).is_dir()
    assert not dirs["dest"].exists() or not any(dirs["dest"].iterdir())


def test_move_and_undo_inside_watch_folder(make_engine, sample_files, dirs):
    e = make_engine(in_place=True)
    e.propose_taxonomy()
    approve_all(e)
    outs = e.execute_ready()
    assert outs and all(o.ok for o in outs)
    alpha = category_by_keyword(e, "alpha")
    moved = list((dirs["watch"] / alpha.relative_path).iterdir())
    assert moved and all(p.is_file() for p in moved)
    # 元に戻せる
    op = e.db.list_operations(10)[0]
    assert e.undo(op.id).ok
    assert (dirs["watch"] / op.src_path.replace("\\", "/").split("/")[-1]).exists()


def test_organized_folders_are_not_scanned_again(make_engine, sample_files, dirs):
    """サブフォルダも対象にしていても、整理済みのフォルダの中は再び整理しない。"""
    e = make_engine(in_place=True, recursive=True)
    e.propose_taxonomy()
    approve_all(e)
    e.execute_ready()
    n_moved = e.stats()["moved"]
    e.scan_now("scan")
    alpha = category_by_keyword(e, "alpha")
    inside = [r for r in e.db.list_files(None) if (dirs["watch"] / alpha.relative_path).as_posix() in r.path.replace("\\", "/")]
    # 整理済みのファイルは「移動済み」の記録だけで、もう一度判定されて別の状態になっていない
    assert inside and all(r.status == "moved" for r in inside)
    assert e.stats()["moved"] == n_moved
    # 整理済みのフォルダに置いたファイルは、整理の対象にしない
    extra = write(dirs["watch"] / alpha.relative_path / "あとから置いたメモ.txt", "Alpha モーター 試験 結果")
    assert not e.safety.check_source(extra, dirs["watch"]).allowed
    e.scan_now("scan")
    assert e.db.get_file(str(extra)) is None
    assert all(str(extra) != i.path for i in e.queue.snapshot())
    # 整理するフォルダの直下のファイルは、これまでどおり対象
    top = write(dirs["watch"] / "新着メモ.txt", "Alpha モーター 試験")
    assert e.safety.check_source(top, dirs["watch"]).allowed


def test_own_folders_in_watch_folder_are_not_reported(make_engine, sample_files, dirs):
    e = make_engine(in_place=True)
    (dirs["watch"] / "私のフォルダ").mkdir()
    e.propose_taxonomy()
    approve_all(e)
    kinds = [m.kind for m in e.consistency()]
    assert "unknown_folder" not in kinds and "missing_folder" not in kinds
    assert (dirs["watch"] / "私のフォルダ").is_dir()


def test_new_watch_folder_gets_category_folders(make_engine, sample_files, dirs):
    e = make_engine(in_place=True)
    e.propose_taxonomy()
    approve_all(e)
    other = dirs["watch"].parent / "Desktop"
    other.mkdir()
    write(other / "memo.txt", "テスト")
    e.add_watch_folder(str(other))
    for c in e.store.taxonomy.active():
        assert (other / c.relative_path).is_dir()

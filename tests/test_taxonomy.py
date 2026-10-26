import json
from pathlib import Path

import pytest

from conftest import approve_all, category_by_keyword, write
from settings.paths import AppPaths
from taxonomy.consistency import apply_fix, check_consistency
from taxonomy.model import Taxonomy, TaxonomyError, apply_operations, new_category_id
from taxonomy.schema import validate_folder_map
from taxonomy.store import TaxonomyStore


def test_ai_generates_taxonomy_from_content(make_engine, sample_files):
    e = make_engine()
    summary = e.propose_taxonomy()
    assert summary["analyzed"] == len(sample_files)
    props = e.db.list_proposals("pending")
    assert len(props) >= 3
    # 提案だけでは正式体系に反映されない
    assert e.store.taxonomy.active() == []
    data = json.loads(e.paths.folder_map_json.read_text(encoding="utf-8"))
    assert data["categories"] == []
    approve_all(e)
    alpha = category_by_keyword(e, "alpha")
    # 拡張子ではなくプロジェクト単位でまとまる（txt / csv / md が同じカテゴリ）
    prop = next(p for p in e.db.list_proposals(None) if p.operations[0]["category"]["category_id"] == alpha.category_id)
    exts = {Path(f).suffix for f in prop.related_files}
    assert {".txt", ".csv", ".md"} <= exts


def test_existing_category_reused_and_new_category_proposed(make_engine, sample_files, dirs):
    e = make_engine()
    e.propose_taxonomy()
    approve_all(e)
    n_before = len(e.store.taxonomy.active())
    # 2 回目の分析では既存カテゴリを再利用し、新しいカテゴリを乱立させない
    summary = e.propose_taxonomy()
    assert summary["reused"] >= 5
    assert e.db.list_proposals("pending") == []
    # 既存カテゴリに合う新規ファイル → そのカテゴリへ
    f = write(dirs["watch"] / "alpha_試験報告_v3.txt", "Alpha モーター 試験 結果 報告")
    out = e.process_file(f, "event")
    assert out.category_id == category_by_keyword(e, "alpha").category_id
    # 合わないファイル → 新規カテゴリの提案（承認まで正式体系は変わらない）
    g = write(dirs["watch"] / "猫の飼育メモ.txt", "猫 キャットフード 動物病院 予防接種 猫砂")
    out = e.process_file(g, "event")
    assert out.status == "review"
    pend = e.db.list_proposals("pending")
    assert len(pend) == 1 and pend[0].kind == "add"
    assert len(e.store.taxonomy.active()) == n_before


def test_save_reload_and_outputs(make_engine, sample_files, dirs):
    e = make_engine()
    e.propose_taxonomy()
    approve_all(e)
    data = json.loads(e.paths.folder_map_json.read_text(encoding="utf-8"))
    validate_folder_map(data)
    for key in ("taxonomy_version", "generated_at", "updated_at", "categories"):
        assert key in data
    c = data["categories"][0]
    for key in ("category_id", "category_name", "parent_category_id", "relative_path", "description",
                "classification_rules", "exclusion_rules", "approval_status", "created_at", "updated_at"):
        assert key in c
    md = e.paths.folder_map_md.read_text(encoding="utf-8")
    assert "フォルダ階層" in md and f"v{data['taxonomy_version']}" in md and c["category_name"] in md
    # 再読み込みしても同じ
    store2 = TaxonomyStore(AppPaths(dirs["data"]))
    assert store2.taxonomy.to_dict() == e.store.taxonomy.to_dict()


def test_category_id_kept_on_rename_and_history(make_engine, sample_files):
    e = make_engine()
    e.propose_taxonomy()
    approve_all(e)
    cat = category_by_keyword(e, "alpha")
    v = e.store.taxonomy.taxonomy_version
    hist_before = len(list(e.paths.history_dir.glob("*.json")))
    ok, _ = e.change_taxonomy([{"op": "rename", "category_id": cat.category_id, "new_name": "Alpha開発"}], "改名テスト")
    assert ok
    renamed = e.store.taxonomy.get(cat.category_id)
    assert renamed.category_name == "Alpha開発" and renamed.relative_path == "Alpha開発"
    assert e.store.taxonomy.taxonomy_version == v + 1
    files = sorted(e.paths.history_dir.glob("*.json"))
    assert len(files) == hist_before + 1
    entry = json.loads(files[-1].read_text(encoding="utf-8"))
    assert entry["from_version"] == v and entry["to_version"] == v + 1 and entry["reason"] == "改名テスト"
    assert entry["operation_types"] == ["rename"]
    # 内容が変わらない変更では書き換えない
    mtime = e.paths.folder_map_json.stat().st_mtime_ns
    ok, msg = e.change_taxonomy([{"op": "rename", "category_id": cat.category_id, "new_name": "Alpha開発"}], "同名")
    assert ok and "変更はありません" in msg
    assert e.paths.folder_map_json.stat().st_mtime_ns == mtime
    assert len(list(e.paths.history_dir.glob("*.json"))) == hist_before + 1


def _add(name, parent=None, kws=("a", "b")):
    cid = new_category_id()
    return cid, {"op": "add", "category": {"category_id": cid, "category_name": name, "parent_category_id": parent,
                                           "classification_rules": {"keywords": list(kws)}}}


def test_merge_split_move_operations():
    a, op_a = _add("A", kws=("x", "y"))
    b, op_b = _add("B", kws=("y", "z"))
    t = apply_operations(Taxonomy(), [op_a, op_b])
    t2 = apply_operations(t, [{"op": "merge", "source_ids": [b], "target_id": a}])
    assert t2.get(b).approval_status == "merged" and t2.get(b).merged_into == a
    assert set(t2.get(a).keywords) == {"x", "y", "z"}
    t3 = apply_operations(t2, [{"op": "split", "category_id": a, "children": [
        {"category_name": "A1", "classification_rules": {"keywords": ["x"]}},
        {"category_name": "A2", "classification_rules": {"keywords": ["z"]}}]}])
    kids = t3.children(a)
    assert [k.relative_path for k in kids] == ["A/A1", "A/A2"]
    c, op_c = _add("C")
    t4 = apply_operations(t3, [op_c, {"op": "move", "category_id": kids[0].category_id, "new_parent_id": c}])
    assert t4.get(kids[0].category_id).relative_path == "C/A1"
    with pytest.raises(TaxonomyError):
        apply_operations(t4, [{"op": "move", "category_id": c, "new_parent_id": kids[0].category_id}])
    _, dup = _add("C")
    with pytest.raises(TaxonomyError):
        apply_operations(t4, [dup])


def test_unapproved_parent_rejected():
    pid = new_category_id()
    _, child = _add("child", parent=pid)
    with pytest.raises(TaxonomyError):
        apply_operations(Taxonomy(), [child])


def test_ai_names_are_sanitized_for_folders():
    _, op = _add('..\\..\\Windows:System*32')
    t = apply_operations(Taxonomy(), [op])
    path = t.active()[0].relative_path
    assert ".." not in path and "\\" not in path and ":" not in path and "*" not in path


def test_consistency_detects_mismatches(make_engine, sample_files, dirs):
    e = make_engine()
    e.propose_taxonomy()
    approve_all(e)
    cats = e.store.taxonomy.active()
    # 1 つだけ実フォルダを作り、別に未登録フォルダも作る
    (dirs["dest"] / cats[0].relative_path).mkdir(parents=True)
    (dirs["dest"] / "手動で作ったフォルダ").mkdir()
    res = check_consistency(e.store.taxonomy, dirs["dest"])
    kinds = [m.kind for m in res]
    assert kinds.count("missing_folder") == len(cats) - 1
    assert "unknown_folder" in kinds
    # 未登録フォルダは削除されない
    assert (dirs["dest"] / "手動で作ったフォルダ").is_dir()
    # 改名後の不一致は「改名案」として出る
    e.change_taxonomy([{"op": "rename", "category_id": cats[0].category_id, "new_name": "改名後"}], "改名")
    res = e.consistency()
    ren = [m for m in res if m.kind == "renamed_folder"]
    assert len(ren) == 1 and ren[0].fix["from"] == cats[0].relative_path
    apply_fix(ren[0].fix, dirs["dest"])
    assert (dirs["dest"] / "改名後").is_dir()
    # 改名先が既にあれば上書きしない
    (dirs["dest"] / "X").mkdir()
    (dirs["dest"] / "Y").mkdir()
    with pytest.raises(ValueError):
        apply_fix({"action": "rename_folder", "from": "X", "to": "Y"}, dirs["dest"])


def test_broken_folder_map_is_backed_up(dirs):
    paths = AppPaths(dirs["data"]).ensure()
    paths.folder_map_json.write_text("{broken", encoding="utf-8")
    store = TaxonomyStore(paths)
    assert store.taxonomy.active() == []
    assert list(paths.taxonomy_dir.glob("folder_map.broken-*.json"))


def test_split_suggestion_from_real_folder(make_engine, dirs):
    e = make_engine()
    cid, op = _add("混在", kws=("alpha", "kyoto"))
    e.change_taxonomy([op], "テスト用")
    folder = dirs["dest"] / "混在"
    for i in range(4):
        write(folder / f"alpha_motor_test_{i}.txt", "Alpha motor torque test report")
        write(folder / f"kyoto_trip_{i}.txt", "Kyoto trip hotel itinerary temple")
    created = e.suggest_taxonomy_changes()
    props = [e.db.get_proposal(p) for p in created]
    assert any(p.kind == "split" for p in props)
    assert e.store.taxonomy.children(cid) == []   # 承認まで反映されない


def _add_proposal(e, name, keywords, files=(), parent=None):
    spec = {"category_id": new_category_id(), "category_name": name, "parent_category_id": parent,
            "description": "", "classification_rules": {"keywords": list(keywords), "keyword_weights": {}},
            "exclusion_rules": [], "examples": []}
    return e.db.add_proposal(kind="add", title=f"新しいフォルダ「{name}」", reason="テスト",
                             operations=[{"op": "add", "category": spec}], related_files=list(files)), spec


def test_approve_all_merges_same_name_proposals(make_engine):
    """同じ名前の提案をまとめて採用しても失敗せず、1 つのフォルダにまとまる。"""
    e = make_engine()
    a, _ = _add_proposal(e, "請求書", ["請求", "金額"])
    b, _ = _add_proposal(e, "請求書", ["振込", "請求"])
    ok, msg = e.approve_proposals([a, b])
    assert ok, msg
    cats = e.store.taxonomy.active()
    assert [c.category_name for c in cats] == ["請求書"]
    assert {"請求", "金額", "振込"} <= set(cats[0].keywords)
    assert e.db.list_proposals("pending") == []
    assert "まとめました" in msg


def test_approve_proposal_with_existing_name_and_child(make_engine):
    """既にあるフォルダと同じ名前の提案（その中のフォルダの提案つき）も採用できる。"""
    e = make_engine()
    first, _ = _add_proposal(e, "旅行", ["旅行", "ホテル"])
    assert e.approve_proposals([first])[0]
    dup, parent_spec = _add_proposal(e, "旅行", ["旅行", "航空券"])
    child, _ = _add_proposal(e, "京都", ["京都", "旅館"], parent=parent_spec["category_id"])
    other, _ = _add_proposal(e, "家計簿", ["家計", "支出"])
    ok, msg = e.approve_proposals([child, dup, other])
    assert ok, msg
    tax = e.store.taxonomy
    assert sorted(c.relative_path for c in tax.active()) == ["家計簿", "旅行", "旅行/京都"]
    assert e.db.list_proposals("pending") == []


def test_approve_all_continues_after_failed_proposal(make_engine):
    """採用できない提案が 1 件あっても、ほかの提案は採用する。"""
    e = make_engine()
    bad, _ = _add_proposal(e, "迷子", ["迷子"], parent="cat_does_not_exist")
    good, _ = _add_proposal(e, "家計簿", ["家計", "支出"])
    ok, msg = e.approve_proposals([bad, good])
    assert ok
    assert [c.category_name for c in e.store.taxonomy.active()] == ["家計簿"]
    assert "採用できなかった" in msg
    assert [p.id for p in e.db.list_proposals("pending")] == [bad]
